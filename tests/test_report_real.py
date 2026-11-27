"""Шаблон отчёта на реальной структуре: настоящий report.json и сквозной путь режима рассуждения до запроса."""
import json
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from agent.llm.cache import DiskCache
from agent.llm.gemini import GeminiClient
from agent.pipeline import Pipeline
from agent.report import render, write_report
from agent.schemas import RunResult

FIX = Path(__file__).parent / "fixtures"
SECTIONS = ["## 1. Паспорт", "## Главное", "## 2. Нарезка", "## 3. Проверка каждого", "## 4. Проверка набора",
            "## 5. Сводка", "## 6. Итог"]


def test_render_saved_real_report_json():
    """report.json настоящего прогона (в его stages нет полей thinking и thinking_zero)."""
    data = json.loads((FIX / "real_report.json").read_text(encoding="utf-8"))
    r = RunResult.model_validate(data)
    md = render(r)
    for h in SECTIONS:
        assert h in md
    for s in r.stages:
        assert f"({s['name']}) |" in md          # «нарезка требований (extract)» в приложении
    assert r.requirements[0].quote.text.split()[0] in md


def test_roundtrip_current_structure(tmp_path):
    from tests.test_core import FIX as SAMPLE, FakeLLM, FakePass

    res = Pipeline(FakeLLM(), passes=[FakePass()]).run(str(SAMPLE))
    md_path, js_path = write_report(res, tmp_path / "r.md")
    again = render(RunResult.model_validate(json.loads(js_path.read_text(encoding="utf-8"))))
    assert again == md_path.read_text(encoding="utf-8")


def test_template_not_read_from_disk_at_render(monkeypatch):
    data = json.loads((FIX / "real_report.json").read_text(encoding="utf-8"))
    r = RunResult.model_validate(data)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("диск недоступен")))
    assert "## 6. Итог" in render(r)


# ---------- THINKING доходит до запроса и до ключа кеша ----------


class Out(BaseModel):
    x: int


class FakeModels:
    """Поддельный SDK: отвечает по схеме запроса и запоминает уровень рассуждения каждого вызова."""

    def __init__(self):
        self.levels, self.max_tokens = [], []

    def generate_content(self, model, contents, config):
        lv = config.thinking_config.thinking_level
        self.levels.append(str(getattr(lv, "value", lv)).lower())
        self.max_tokens.append(config.max_output_tokens)
        if "requirements" in json.dumps(config.response_json_schema):
            text = json.dumps({"requirements": [{"source_id": "Т-1", "section": "Требования", "summary": "s",
                                                 "quote": {"text": "Бонусы сгорают через 12 месяцев.",
                                                           "block_id": "B-005"}}]}, ensure_ascii=False)
        else:
            text = '{"x": 1}'
        return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason="STOP")],
                               usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                                              thoughts_token_count=100))


def probe(name, thinking=None, max_out=None):
    mod = SimpleNamespace(NAME=name)
    if thinking:
        mod.THINKING = thinking
    if max_out:
        mod.MAX_OUTPUT_TOKENS = max_out

    def run(client, document_text, requirements):
        client.complete("s", f"проход {name}", Out, name)
        return []
    mod.run = run
    return mod


def test_thinking_attribute_reaches_request_and_cache_key(tmp_path):
    from tests.test_core import FIX as SAMPLE

    fake = FakeModels()
    client = lambda: GeminiClient("test", ["gemini-3.8-flash"], 0, DiskCache(tmp_path),  # noqa: E731
                                  _client=SimpleNamespace(models=fake))
    passes = [probe("individual"), probe("cross_parts"), probe("custom", "off")]
    res = Pipeline(client(), passes=passes, rules=False).run(str(SAMPLE))
    # extract off, individual off (по умолчанию), cross_parts on (по умолчанию), custom off (атрибут)
    assert sorted(fake.levels) == ["high", "low", "low", "low"]
    assert [s["thinking"] for s in res.stages] == ["off", "off", "high", "off"]
    assert [c["thinking"] for s in res.stages for c in s["call_log"]] == ["off", "off", "high", "off"]

    # тот же прогон — всё из кеша; смена режима одного прохода — промах кеша только у него
    fake.levels.clear()
    Pipeline(client(), passes=passes, rules=False).run(str(SAMPLE))
    assert fake.levels == []
    swapped = [probe("individual", "high"), probe("cross_parts"), probe("custom", "off")]
    Pipeline(client(), passes=swapped, rules=False).run(str(SAMPLE))
    assert fake.levels == ["high"]


def test_levels_and_max_output_per_pass_reach_request_cache_and_stages(tmp_path):
    from tests.test_core import FIX as SAMPLE

    fake = FakeModels()
    client = lambda: GeminiClient("test", ["gemini-3.8-flash"], 0, DiskCache(tmp_path),  # noqa: E731
                                  max_output_tokens=32768, _client=SimpleNamespace(models=fake))
    passes = [probe("a", "low"), probe("b", "medium", 8000), probe("c", "high"), probe("d", "on", 4096)]
    res = Pipeline(client(), passes=passes, rules=False).run(str(SAMPLE))
    st = {s["name"]: s for s in res.stages}
    # проходы a–d идут параллельно: уровень и лимит сверяются по проходу, а не по порядку прихода запросов
    got = {s["name"]: (s["call_log"][0]["thinking"], s["call_log"][0]["max_output_tokens"]) for s in res.stages}
    assert got == {"extract": ("off", 32768), "a": ("low", 32768), "b": ("medium", 8000),
                   "c": ("high", 32768), "d": ("high", 4096)}                   # on -> high
    assert sorted(zip(fake.levels, fake.max_tokens)) == sorted(
        [("low", 32768), ("low", 32768), ("medium", 8000), ("high", 32768), ("high", 4096)])
    assert (st["b"]["thinking"], st["b"]["max_output_tokens"]) == ("medium", 8000)
    assert (st["c"]["thinking"], st["c"]["max_output_tokens"]) == ("high", 32768)
    assert st["d"]["thinking"] == "high" and st["b"]["call_log"][0]["max_output_tokens"] == 8000

    # тот же прогон — из кеша; другой лимит выхода у одного прохода — промах только у него
    fake.levels.clear()
    Pipeline(client(), passes=passes, rules=False).run(str(SAMPLE))
    assert fake.levels == []
    changed = [probe("a", "low"), probe("b", "medium", 9000), probe("c", "high"), probe("d", "on", 4096)]
    Pipeline(client(), passes=changed, rules=False).run(str(SAMPLE))
    assert fake.max_tokens[-1] == 9000 and len(fake.levels) == 1


def test_invalid_pass_attributes_rejected():
    import pytest
    from agent.pipeline import max_output_tokens, thinking_mode

    with pytest.raises(ValueError, match="THINKING"):
        thinking_mode(SimpleNamespace(NAME="x", THINKING="max"))
    with pytest.raises(ValueError, match="MAX_OUTPUT_TOKENS"):
        max_output_tokens(SimpleNamespace(NAME="x", MAX_OUTPUT_TOKENS=0))

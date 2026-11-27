"""Время прогона по часам (от старта до записи отчёта) и сумма длительностей вызовов — на заглушке, без сети."""
import json
import time

from pydantic import BaseModel

from agent.llm.base import BaseLLMClient, CallResult
from agent.llm.cache import DiskCache
from agent.pipeline import Pipeline, totals_line
from agent.report import write_report
from tests.test_core import FIX, FakeLLM

CALL = 0.2   # длительность одного вызова заглушки


class Out(BaseModel):
    x: int


class SleepyLLM(BaseLLMClient):
    """Каждый вызов длится CALL секунд; ответ нарезки берётся у FakeLLM."""
    name = "fake"

    def __init__(self, tmp_path):
        super().__init__(["fake-1"], 0, DiskCache(tmp_path), concurrency=3)

    def _call(self, model, system, user, schema):
        time.sleep(CALL)
        if schema is Out:
            return CallResult(text='{"x": 1}', tokens_in=10, tokens_out=1)
        return CallResult(text=FakeLLM().complete("", "", schema, "").model_dump_json(), tokens_in=10, tokens_out=1)


def one_call_pass(name):
    class P:
        NAME = name

        @staticmethod
        def run(client, document_text, requirements):
            client.complete("s", name, Out, name)
            return []
    return P


def test_wall_time_until_report_written_and_calls_sum_separate(tmp_path, capsys):
    started = time.monotonic()
    pipe = Pipeline(SleepyLLM(tmp_path / "cache"), passes=[one_call_pass(f"p{i}") for i in range(3)], rules=False)
    res = pipe.run(str(FIX))
    time.sleep(0.3)                                   # работа после проходов (сборка отчёта) входит в время прогона
    md, js = write_report(res, tmp_path / "r.md", started)
    pipe.log_totals(res)

    # 4 вызова по 0.2 с: нарезка, затем три прохода параллельно
    assert 0.8 <= res.calls_seconds < 1.0
    assert res.wall_seconds >= 0.4 + 0.3              # нарезка + параллельная группа + пауза до записи
    assert res.wall_seconds < res.calls_seconds + 0.3  # параллельные вызовы не складываются во время прогона

    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["wall_seconds"] == res.wall_seconds and data["calls_seconds"] == res.calls_seconds

    text = md.read_text(encoding="utf-8")
    assert f"| Время прогона | {res.wall_seconds:.1f} с — по часам, от старта до записи отчёта |" in text
    assert f"| Сумма длительностей вызовов | {res.calls_seconds:.1f} с — " in text

    err = capsys.readouterr().err
    last = [ln for ln in err.splitlines() if ln.startswith("Итого:")]
    assert last == [totals_line(res)]                 # итоговая строка одна и печатается после записи отчёта
    assert f"время прогона {res.wall_seconds:.0f} с (по часам, от старта до записи отчёта)" in last[0]
    assert f"сумма длительностей вызовов {res.calls_seconds:.0f} с" in last[0]


def test_report_without_wall_time_still_renders(tmp_path):
    res = Pipeline(FakeLLM(), passes=[], rules=False).run(str(FIX))
    res.wall_seconds = None                           # старый report.json без поля
    md, _ = write_report(res, tmp_path / "r.md")
    assert "| Время прогона | — |" in md.read_text(encoding="utf-8")

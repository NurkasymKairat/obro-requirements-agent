"""Клиент модели на заглушках: повторы, запасные модели, кеш, токены, баланс. Сеть не используется."""
from types import SimpleNamespace

import pytest
from google.genai import errors
from pydantic import BaseModel

from agent.llm import base
from agent.llm.base import (AllModelsUnavailable, BalanceExhausted, BaseLLMClient, CallResult, ModelUnavailable,
                            RetryableError)
from agent.llm.cache import DiskCache
from agent.llm.gemini import GeminiClient


class Out(BaseModel):
    x: int


def ok(x, tin=100, tout=10, think=50):
    return CallResult(text=f'{{"x": {x}}}', tokens_in=tin, tokens_out=tout, tokens_thinking=think)


class Scripted(BaseLLMClient):
    name = "fake"

    def __init__(self, models, script, cache):
        super().__init__(models, rpm=0, cache=cache, switch_after=3)
        self.script = script          # model -> список исходов: исключение или CallResult
        self.seen = []

    def _call(self, model, system, user, schema):
        self.seen.append(model)
        outcome = self.script[model].pop(0) if self.script[model] else RetryableError("503")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


def test_three_retries_then_next_model(tmp_path):
    c = Scripted(["a", "b"], {"a": [], "b": [ok(1)]}, DiskCache(tmp_path))
    assert c.complete("s", "u", Out, "extract").x == 1
    assert c.seen == ["a"] * 4 + ["b"]                    # 1 + 3 повтора на a, затем b
    assert c.switches[0]["from"] == "a" and c.switches[0]["to"] == "b"
    assert c.stats["extract"]["models"] == {"b": 1}
    assert c.model == "b"                                 # переход до конца прогона


def test_unavailable_switches_immediately(tmp_path):
    c = Scripted(["a", "b"], {"a": [ModelUnavailable("404")], "b": [ok(2)]}, DiskCache(tmp_path))
    assert c.complete("s", "u", Out, "p").x == 2 and c.seen == ["a", "b"]


def test_all_models_unavailable(tmp_path):
    c = Scripted(["a", "b"], {"a": [], "b": []}, DiskCache(tmp_path))
    with pytest.raises(AllModelsUnavailable, match="Все модели недоступны"):
        c.complete("s", "u", Out, "p")


def test_balance_stops_without_switching(tmp_path):
    c = Scripted(["a", "b"], {"a": [BalanceExhausted("402")], "b": [ok(1)]}, DiskCache(tmp_path))
    with pytest.raises(BalanceExhausted):
        c.complete("s", "u", Out, "p")
    assert c.seen == ["a"] and c.switches == []


def test_cache_only_from_requested_model(tmp_path):
    cache = DiskCache(tmp_path)
    first = Scripted(["a", "b"], {"a": [], "b": [ok(3)]}, cache)   # a не ответила, ответ дала b
    first.complete("s", "u", Out, "p")
    again = Scripted(["a", "b"], {"a": [ok(7)], "b": []}, cache)   # новый прогон начинается с a
    assert again.complete("s", "u", Out, "p").x == 7               # ответ b из кеша не подмешан
    assert again.seen == ["a"] and again.stats["p"]["cache_hits"] == 0
    same = Scripted(["a", "b"], {"a": [], "b": []}, cache)
    assert same.complete("s", "u", Out, "p").x == 7 and same.seen == []   # повтор той же модели — из кеша


def test_cache_used_after_switch_to_model_that_answered_before(tmp_path):
    cache = DiskCache(tmp_path)
    Scripted(["a", "b"], {"a": [], "b": [ok(3)]}, cache).complete("s", "u", Out, "p")
    c = Scripted(["a", "b"], {"a": [], "b": []}, cache)             # a снова падает
    assert c.complete("s", "u", Out, "p").x == 3
    assert c.seen == ["a"] * 4 and c.stats["p"]["cache_hits"] == 1   # b не вызывалась: её ответ в кеше


def test_tokens_logged_per_call_and_summed(tmp_path):
    bad = CallResult(text="", tokens_in=100, tokens_out=0, tokens_thinking=30, problem="обрезан")
    c = Scripted(["a"], {"a": [bad, ok(1, 100, 10, 50)]}, DiskCache(tmp_path))
    c.complete("s", "u", Out, "p")
    st = c.stats["p"]
    assert [e["status"][:2] for e in st["call_log"]] == ["от", "ok"]
    assert (st["tokens_in"], st["tokens_out"], st["tokens_thinking"]) == (200, 10, 80)  # оплаченный брак учтён
    assert c.total.calls == 2 and c.total.tokens["tokens_thinking"] == 80


# ---------- Gemini на поддельном SDK-клиенте ----------


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.configs = []

    def generate_content(self, model, contents, config):
        self.configs.append(config)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def gresp(text='{"x": 5}', finish="STOP", tin=1000, tout=20, think=300):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)],
                           usage_metadata=SimpleNamespace(prompt_token_count=tin, candidates_token_count=tout,
                                                          thoughts_token_count=think))


def level(cfg) -> str:
    lv = cfg.thinking_config.thinking_level
    return str(getattr(lv, "value", lv)).lower()


def gemini(tmp_path, outcomes, **kw):
    fake = FakeModels(outcomes)
    c = GeminiClient("test", ["gemini-3.8-flash", "gemini-3.6-flash"], 0, DiskCache(tmp_path),
                     _client=SimpleNamespace(models=fake), **kw)
    return c, fake


def test_gemini_params_and_usage(tmp_path):
    c, fake = gemini(tmp_path, [gresp(), gresp()])
    with c.using_thinking("on"):
        assert c.complete("s", "u", Out, "p").x == 5
    with c.using_thinking("off"):
        c.complete("s", "u2", Out, "p")
    on, off = fake.configs
    assert on.temperature == 0 and on.seed == 42 and on.response_mime_type == "application/json"
    assert level(on) == "high" and on.thinking_config.thinking_budget is None
    assert level(off) == "low"
    st = c.stats["p"]
    assert (st["tokens_in"], st["tokens_out"], st["tokens_thinking"]) == (2000, 40, 600)
    assert [e["thinking"] for e in st["call_log"]] == ["on", "off"]


def test_gemini_budget_zero_forces_off(tmp_path):
    c, fake = gemini(tmp_path, [gresp()], thinking_budget=0)
    with c.using_thinking("on"):
        c.complete("s", "u", Out, "p")
    assert level(fake.configs[0]) == "low"


def test_warns_when_thinking_requested_but_zero(tmp_path, capsys):
    c, _ = gemini(tmp_path, [gresp(think=0), gresp(think=0)])
    with c.using_thinking("on"):
        c.complete("s", "u", Out, "p")
    with c.using_thinking("off"):
        c.complete("s", "u2", Out, "p")
    assert c.stats["p"]["thinking_zero"] == 1                       # только для on
    assert "рассуждение запрошено, но по usage модель не рассуждала" in capsys.readouterr().err


def test_gemini_402_is_balance(tmp_path):
    err = errors.APIError(402, {"error": {"code": 402, "message": "Insufficient balance"}})
    c, _ = gemini(tmp_path, [err])
    with pytest.raises(BalanceExhausted, match="баланс"):
        c.complete("s", "u", Out, "p")


def test_gemini_drops_rejected_thinking_param(tmp_path):
    err = errors.ClientError(400, {"error": {"code": 400, "message": "thinking_budget is not supported"}})
    c, fake = gemini(tmp_path, [err, gresp()])
    assert c.complete("s", "u", Out, "p").x == 5
    assert fake.configs[0].thinking_config is not None and fake.configs[1].thinking_config is None
    assert c.stats["p"]["thinking_zero"] == 0           # параметр снят — рассуждения не требуем


def test_gemini_truncated_answer_counts_tokens(tmp_path):
    c, _ = gemini(tmp_path, [gresp(text='{"x":', finish="MAX_TOKENS"), gresp()])
    c.complete("s", "u", Out, "p")
    assert c.stats["p"]["tokens_in"] == 2000 and c.stats["p"]["calls"] == 2


def test_thinking_mode_changes_cache_key(tmp_path):
    c, _ = gemini(tmp_path, [])
    with c.using_thinking("on"):
        k_on = c._key("gemini-3.8-flash", "s", "u", Out)
    with c.using_thinking("off"):
        k_off = c._key("gemini-3.8-flash", "s", "u", Out)
    assert k_on != k_off


def test_config_defaults(monkeypatch, tmp_path):
    from agent.config import load_settings

    monkeypatch.chdir(tmp_path)
    for v in ("GEMINI_MODEL", "GEMINI_FALLBACK_MODELS", "GEMINI_THINKING_BUDGET", "CACHE_DIR", "LLM_PROVIDER"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    s = load_settings()
    assert s.gemini_model == "gemini-3.8-flash"
    assert s.gemini_fallback_models == ["gemini-3.6-flash", "gemini-3.5-flash"]
    assert s.gemini_thinking_budget == 2048 and str(s.cache_dir) == ".cache/llm"
    monkeypatch.setenv("CACHE_DIR", "/tmp/x-cache")
    assert str(load_settings().cache_dir) == "/tmp/x-cache"


def test_gemini_levels_and_rejected_medium_falls_back_to_low(tmp_path):
    err = errors.ClientError(400, {"error": {"code": 400, "message": "Thinking level MEDIUM is not supported"}})
    c, fake = gemini(tmp_path, [gresp(), err, gresp(), gresp()])
    with c.using_pass(thinking="high"):
        c.complete("s", "u1", Out, "p")
    with c.using_pass(thinking="medium"):
        c.complete("s", "u2", Out, "p")                     # medium отвергнут -> повтор с low
    with c.using_pass(thinking="medium"):
        c.complete("s", "u3", Out, "p")                     # запомнено: сразу low
    assert [level(cfg) for cfg in fake.configs] == ["high", "medium", "low", "low"]
    assert all(cfg.thinking_config is not None for cfg in fake.configs)   # рассуждение не снято целиком


def test_using_pass_restores_and_validates(tmp_path):
    c, _ = gemini(tmp_path, [])
    with c.using_pass(thinking="low", max_output_tokens=1000):
        assert c.thinking == "low" and c.max_output_tokens_effective == 1000
    assert c.thinking == "on" and c.max_output_tokens_effective == 32768
    with pytest.raises(Exception, match="THINKING"):
        with c.using_pass(thinking="max"):
            pass


# ---------- тайм-аут запроса по проходам и недоступный бесплатный тариф ----------


def test_timeout_depends_on_pass_thinking(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TIMEOUT", raising=False)
    monkeypatch.delenv("AGENT_TIMEOUT_THINKING", raising=False)
    c, fake = gemini(tmp_path, [gresp(), gresp(), gresp()])
    for i, mode in enumerate(("off", "medium", "high")):
        with c.using_pass(thinking=mode):
            c.complete("s", f"u{i}", Out, "p")
    assert [cfg.http_options.timeout for cfg in fake.configs] == [90_000, 360_000, 360_000]


def test_timeout_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT", "30")
    monkeypatch.setenv("AGENT_TIMEOUT_THINKING", "200")
    c, fake = gemini(tmp_path, [gresp(), gresp()], thinking_budget=0)
    with c.using_pass(thinking="off"):
        c.complete("s", "u", Out, "p")
    with c.using_pass(thinking="high"):        # потолок 0 переводит проход в минимальное рассуждение
        c.complete("s", "u2", Out, "p")
    assert [cfg.http_options.timeout for cfg in fake.configs] == [30_000, 30_000]
    monkeypatch.setenv("AGENT_TIMEOUT", "abc")
    from agent.config import ConfigError
    with pytest.raises(ConfigError, match="AGENT_TIMEOUT"):
        gemini(tmp_path, [])


def test_gemini_timeout_retries_then_next_model(tmp_path):
    class ReadTimeout(Exception):
        pass
    ReadTimeout.__module__ = "httpx2"
    gone = errors.ServerError(504, {"error": {"code": 504, "message": "Deadline expired", "status": "DEADLINE_EXCEEDED"}})
    c, fake = gemini(tmp_path, [ReadTimeout(), gone, gresp()])
    with c.using_pass(thinking="off"):
        assert c.complete("s", "u", Out, "extract").x == 5
    log = c.stats["extract"]["call_log"]
    assert [e["model"] for e in log] == ["gemini-3.8-flash"] * 2 + ["gemini-3.6-flash"]   # зависание, один повтор, запасная
    assert "тайм-аут запроса, 90 с" in log[0]["status"] and "504, тайм-аут" in log[1]["status"]
    assert c.stats["extract"]["retries"] == 1
    assert c.model == "gemini-3.6-flash" and "тайм-аут" in c.switches[0]["reason"]   # для всех следующих вызовов


def test_gemini_free_tier_unavailable_stops_at_once(tmp_path):
    from agent.llm import FreeTierUnavailable
    err = errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                             "message": "The Gemini API free tier is not available for this project."}})
    c, fake = gemini(tmp_path, [err])
    with pytest.raises(FreeTierUnavailable, match="недоступен бесплатный тариф") as ei:
        c.complete("s", "u", Out, "p")
    assert "новом проекте AI Studio" in str(ei.value) and "включите оплату" in str(ei.value)
    assert isinstance(ei.value, BalanceExhausted) and ei.value.exit_code == 6     # прогон останавливается целиком
    assert len(fake.configs) == 1 and c.switches == [] and c.stats["p"]["retries"] == 0
    assert c.model == "gemini-3.8-flash"


def test_gemini_other_403_is_not_free_tier(tmp_path):
    err = errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "API key not valid"}})
    c, _ = gemini(tmp_path, [err])
    with pytest.raises(base.LLMError, match="Gemini 403") as ei:
        c.complete("s", "u", Out, "p")
    assert not isinstance(ei.value, BalanceExhausted)


def test_hang_one_retry_then_next_model_without_pause(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(base.time, "sleep", slept.append)
    c = Scripted(["a", "b"], {"a": [base.timeout_error(90), base.timeout_error(90)], "b": [ok(1)]}, DiskCache(tmp_path))
    assert c.complete("s", "u", Out, "extract").x == 1
    assert c.seen == ["a", "a", "b"]                      # зависание → один повтор → запасная модель
    assert sum(slept) == 0 and c.model == "b" and "тайм-аут" in c.switches[0]["reason"]


def test_503_still_three_retries_then_next_model(tmp_path):
    c = Scripted(["a", "b"], {"a": [RetryableError("503, сервис перегружен")] * 4, "b": [ok(1)]}, DiskCache(tmp_path))
    assert c.complete("s", "u", Out, "extract").x == 1
    assert c.seen == ["a"] * 4 + ["b"] and c.stats["extract"]["retries"] == 3


def test_hang_counter_resets_after_success(tmp_path):
    c = Scripted(["a", "b"], {"a": [base.timeout_error(90), ok(1), base.timeout_error(90), ok(2)], "b": []},
                 DiskCache(tmp_path))
    assert c.complete("s", "u1", Out, "p").x == 1 and c.complete("s", "u2", Out, "p").x == 2
    assert c.model == "a" and c.switches == []            # одиночные зависания модель не меняют


def test_exit_code_for_free_tier():
    from agent.__main__ import exit_code
    from agent.llm import FreeTierUnavailable
    incomplete = SimpleNamespace(incomplete_reason="остановлено")
    assert exit_code(incomplete, FreeTierUnavailable("403")) == 6
    assert exit_code(incomplete, BalanceExhausted("402")) == 5
    assert exit_code(SimpleNamespace(incomplete_reason=None), None) == 0

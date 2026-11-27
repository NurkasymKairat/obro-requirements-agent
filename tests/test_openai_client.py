"""Клиент OpenAI на заглушке HTTP: настоящий SDK, подменён только транспорт. Реальных вызовов нет."""
import json

import httpx2
import pytest
from pydantic import BaseModel

from agent.llm import base
from agent.llm.base import LLMError
from agent.llm.cache import DiskCache
from agent.llm.openai import OpenAIClient
from agent.schemas import RawRequirement


class Out(BaseModel):
    requirements: list[RawRequirement]


ANSWER = {"requirements": [{"source_id": None, "section": "S", "summary": "s",
                            "quote": {"text": "t", "block_id": "B-001"}}]}


def ok(model, payload=ANSWER, status="completed"):
    return httpx2.Response(200, json={
        "id": "resp_1", "object": "response", "created_at": 0, "model": model, "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(payload), "annotations": []}]}],
    })


def err(code, message="x", err_code=None, param=None):
    return httpx2.Response(code, json={"error": {"message": message, "type": "error", "code": err_code, "param": param}})


class Stub:
    """Транспорт: по модели из тела запроса отдаёт следующий заготовленный ответ."""

    def __init__(self, script):
        self.script = script
        self.requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request, body))
        queue = self.script[body["model"]]
        r = queue.pop(0) if queue else err(503, "overloaded")
        return r(body["model"]) if callable(r) else r


def client(tmp_path, script, models=("gpt-6-sol", "gpt-6-luna")):
    stub = Stub(script)
    c = OpenAIClient("test-key-not-real", list(models), rpm=0, cache=DiskCache(tmp_path), switch_after=3,
                     reasoning_effort="medium", http_client=httpx2.Client(transport=httpx2.MockTransport(stub)))
    return c, stub


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


def test_strict_schema_and_params(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [ok]})
    res = c.complete("system", "user", Out, "extract")
    assert res.requirements[0].quote.block_id == "B-001"
    req, body = stub.requests[0]
    assert req.url.path.endswith("/responses")
    assert req.headers["authorization"] == "Bearer test-key-not-real"
    assert body["instructions"] == "system" and body["input"] == "user"
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "Out"
    assert fmt["schema"]["additionalProperties"] is False
    assert body["reasoning"] == {"effort": "medium"}
    assert "temperature" not in body and body["store"] is False


def test_cache_resumes_without_http(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [ok]})
    c.complete("s", "u", Out, "p")
    c2, stub2 = client(tmp_path, {"gpt-6-sol": []})
    assert c2.complete("s", "u", Out, "p").requirements and stub2.requests == []
    assert c2.stats["p"]["cache_hits"] == 1


def test_cache_key_includes_provider_and_model(tmp_path):
    c, _ = client(tmp_path, {})
    k = c._key("gpt-6-sol", "s", "u", Out)
    assert k != c._key("gpt-6-luna", "s", "u", Out)
    c.name = "gemini"
    assert k != c._key("gpt-6-sol", "s", "u", Out)


def test_503_three_retries_then_fallback(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [], "gpt-6-luna": [ok]})
    c.complete("s", "u", Out, "p")
    assert [b["model"] for _, b in stub.requests] == ["gpt-6-sol"] * 4 + ["gpt-6-luna"]
    assert c.switches[0]["to"] == "gpt-6-luna" and c.stats["p"]["models"] == {"gpt-6-luna": 1}


def test_404_switches_immediately_and_no_balance_stops(tmp_path):
    from agent.llm.base import BalanceExhausted

    c, stub = client(tmp_path, {"gpt-6-sol": [err(404, "no model")], "gpt-6-luna": [ok]})
    c.complete("s", "u", Out, "p")
    assert len(stub.requests) == 2
    for e in (err(429, "quota", "insufficient_quota"), err(402, "payment required")):
        c, stub = client(tmp_path / str(e.status_code), {"gpt-6-sol": [e], "gpt-6-luna": [ok]})
        with pytest.raises(BalanceExhausted, match="баланс"):
            c.complete("s", "u", Out, "p")
        assert len(stub.requests) == 1                    # баланс общий: на запасную модель не переходим


def test_usage_split_into_output_and_reasoning(tmp_path):
    def with_usage(model):
        r = ok(model)
        body = json.loads(r.content)
        body["usage"] = {"input_tokens": 1000, "output_tokens": 300, "total_tokens": 1300,
                         "input_tokens_details": {"cached_tokens": 0},
                         "output_tokens_details": {"reasoning_tokens": 200}}
        return httpx2.Response(200, json=body)

    c, _ = client(tmp_path, {"gpt-6-sol": [with_usage]})
    c.complete("s", "u", Out, "p")
    st = c.stats["p"]
    assert (st["tokens_in"], st["tokens_out"], st["tokens_thinking"]) == (1000, 100, 200)


def test_reasoning_dropped_when_unsupported(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [err(400, "Unsupported parameter: 'reasoning'", param="reasoning"), ok]})
    c.complete("s", "u", Out, "p")
    assert "reasoning" in stub.requests[0][1] and "reasoning" not in stub.requests[1][1]


def test_bad_key_is_fatal_and_incomplete_is_bad_output(tmp_path):
    c, _ = client(tmp_path, {"gpt-6-sol": [err(401, "bad key")]})
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        c.complete("s", "u", Out, "p")
    c, stub = client(tmp_path / "i", {"gpt-6-sol": [lambda m: ok(m, status="incomplete")] * 3})
    with pytest.raises(LLMError, match="не по схеме"):
        c.complete("s", "u", Out, "p")
    assert len(stub.requests) == 3


def test_factory_selects_provider(monkeypatch, tmp_path):
    from agent.config import load_settings
    from agent.llm import make_client

    monkeypatch.chdir(tmp_path)                      # без .env
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("OPENAI_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    c = make_client(load_settings())
    assert c.name == "openai" and c.models == ["gpt-6-sol", "gpt-6-luna"] and c.reasoning_effort == "medium"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    assert make_client(load_settings()).name == "gemini"


def test_thinking_off_lowers_reasoning_effort(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [ok, ok]})
    with c.using_thinking("off"):
        c.complete("s", "u", Out, "p")
    with c.using_thinking("on"):
        c.complete("s", "u2", Out, "p")
    assert [b["reasoning"]["effort"] for _, b in stub.requests] == ["low", "medium"]


def test_thinking_levels_map_to_effort(tmp_path):
    c, stub = client(tmp_path, {"gpt-6-sol": [ok, ok, ok, ok]})
    for i, lv in enumerate(("off", "low", "medium", "high")):
        with c.using_pass(thinking=lv, max_output_tokens=5000):
            c.complete("s", f"u{i}", Out, "p")
    assert [b["reasoning"]["effort"] for _, b in stub.requests] == ["low", "low", "medium", "high"]
    assert {b["max_output_tokens"] for _, b in stub.requests} == {5000}


def test_timeout_per_pass_and_retry_then_next_model(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TIMEOUT", raising=False)
    monkeypatch.delenv("AGENT_TIMEOUT_THINKING", raising=False)
    left = {"gpt-6-sol": 2}                   # столько запросов к основной модели зависнут
    seen = []

    def transport(request):
        model = json.loads(request.content)["model"]
        seen.append((model, request.extensions["timeout"]["read"]))
        if left.get(model, 0) > 0:
            left[model] -= 1
            raise httpx2.ReadTimeout("timed out", request=request)
        return ok(model)

    c = OpenAIClient("test-key-not-real", ["gpt-6-sol", "gpt-6-luna"], rpm=0, cache=DiskCache(tmp_path),
                     switch_after=3, http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
    with c.using_pass(thinking="off"):
        assert c.complete("s", "u", Out, "extract").requirements
    with c.using_pass(thinking="high"):
        c.complete("s", "u2", Out, "review")
    # зависание, один повтор на основной, затем запасная — и для следующего прохода тоже
    assert seen == [("gpt-6-sol", 90.0)] * 2 + [("gpt-6-luna", 90.0), ("gpt-6-luna", 360.0)]
    assert "тайм-аут запроса, 90 с" in c.stats["extract"]["call_log"][0]["status"]
    assert c.model == "gpt-6-luna" and "тайм-аут" in c.switches[0]["reason"]

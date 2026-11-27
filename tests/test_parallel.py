"""Параллельные вызовы на заглушках: порядок результатов, ограничение параллельности, сбой одного вызова,
общий переход на запасную модель, паузы под RPM. Сеть не используется."""
import threading
import time

import pytest
from pydantic import BaseModel

from agent.llm.base import BaseLLMClient, CallResult, RetryableError
from agent.llm.cache import DiskCache
from agent.parallel import pmap
from agent.pipeline import Pipeline, pass_groups
from agent.schemas import RawFinding, RawQuote
from tests.test_core import FIX, FakeLLM


class Out(BaseModel):
    x: int


class Slow(BaseLLMClient):
    """Заглушка API: задержка и исход задаются по тексту запроса; считает одновременные вызовы."""
    name = "fake"

    def __init__(self, tmp_path, script=None, delays=None, models=("a",), concurrency=3, rpm=0):
        super().__init__(list(models), rpm, DiskCache(tmp_path), switch_after=3, concurrency=concurrency)
        self.script = script or {}
        self.delays = delays or {}
        self.active = self.max_active = 0
        self.starts, self.seen = [], []
        self._probe_lock = threading.Lock()

    def _call(self, model, system, user, schema):
        with self._probe_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.starts.append(time.monotonic())
            self.seen.append((model, user))
            queue = self.script.get((model, user)) or self.script.get(model)
            outcome = queue.pop(0) if queue else None
        try:
            time.sleep(self.delays.get(user, 0.02))
            if isinstance(outcome, Exception):
                raise outcome
            return CallResult(text=f'{{"x": {len(user)}}}', tokens_in=10, tokens_out=1)
        finally:
            with self._probe_lock:
                self.active -= 1


# ---------- ограничение параллельности и паузы ----------


def test_concurrency_limit_respected(tmp_path):
    c = Slow(tmp_path, concurrency=2, delays={f"u{i}": 0.05 for i in range(6)})
    res = pmap(lambda i: c.complete("s", f"u{i}", Out, "p").x, range(6))
    assert res == [2] * 6
    assert c.max_active == 2                       # параллельно, но не больше AGENT_CONCURRENCY


def test_rpm_slots_spaced_across_threads(tmp_path):
    c = Slow(tmp_path, concurrency=4, rpm=600, delays={f"u{i}": 0.0 for i in range(4)})   # не чаще раза в 0.1 с
    pmap(lambda i: c.complete("s", f"u{i}", Out, "p"), range(4))
    starts = sorted(c.starts)
    assert all(b - a >= 0.09 for a, b in zip(starts, starts[1:]))


# ---------- сбой одного вызова из трёх ----------


def test_one_of_three_gets_429_others_continue(tmp_path):
    script = {("a", "u1"): [RetryableError("429, лимит запросов в минуту", retry_after=0.3)]}
    c = Slow(tmp_path, script=script, delays={"u0": 0.02, "u1": 0.02, "u2": 0.02})
    t0 = time.monotonic()
    done_at = {}

    def call(i):
        r = c.complete("s", f"u{i}", Out, f"p{i}").x
        done_at[i] = time.monotonic() - t0
        return r

    assert pmap(call, range(3)) == [2, 2, 2]                  # все дошли, результаты в порядке входа
    assert c.stats["p1"]["retries"] == 1 and c.stats["p1"]["calls"] == 2
    assert c.stats["p0"]["calls"] == 1 and c.stats["p2"]["calls"] == 1
    assert done_at[0] < 0.25 and done_at[2] < 0.25 <= done_at[1]   # остальные не ждали повтора u1
    assert c.switches == [] and c.model == "a"                # один 429 — не повод менять модель


def test_fallback_switch_is_shared_by_concurrent_calls(tmp_path):
    fail = RetryableError("503", retry_after=0.0)
    c = Slow(tmp_path, script={"a": [fail] * 50}, models=("a", "b"), concurrency=3)
    assert pmap(lambda i: c.complete("s", f"u{i}", Out, "p").x, range(3)) == [2, 2, 2]
    calls_a = sum(1 for m, _ in c.seen if m == "a")
    assert len(c.switches) == 1 and c.model == "b"            # переход один на всех
    assert calls_a <= c.switch_after + c.concurrency          # не 3 вызова × 4 попытки = 12
    assert sum(1 for m, _ in c.seen if m == "b") == 3


# ---------- детерминированный порядок находок и номеров ----------


def raw(desc, quote):
    return RawFinding(scope="set", requirement_ids=[], criterion="полнота", standard="ISO/IEC/IEEE 29148 п. 5.2.6",
                      kind="incomplete", severity="medium", description=desc, fix="Дописать.",
                      quotes=[RawQuote(text=quote, block_id="")])


def timed_pass(name, delay, quote):
    class P:
        NAME = name

        @staticmethod
        def run(client, document_text, requirements):
            time.sleep(delay)
            return [raw(f"{name}: находка", quote)]
    return P


QUOTES = ["Бонусы сгорают через 12 месяцев.", "Покупка 1 000 ₸", "Начислено 10 бонусов"]


def findings_of(delays):
    passes = [timed_pass(n, d, q) for n, d, q in zip(("p1", "p2", "p3"), delays, QUOTES)]
    assert len(pass_groups(passes)) == 1                          # одна параллельная группа
    res = Pipeline(FakeLLM(), passes=passes, rules=False).run(str(FIX))
    return [(f.fid, f.description) for f in res.findings], [s["name"] for s in res.stages]


def test_findings_order_and_ids_do_not_depend_on_completion_order():
    first_slow, stages1 = findings_of([0.15, 0.08, 0.0])          # p1 возвращается последним
    last_slow, stages2 = findings_of([0.0, 0.08, 0.15])           # p3 возвращается последним
    assert first_slow == last_slow == [("F-001", "p1: находка"), ("F-002", "p2: находка"), ("F-003", "p3: находка")]
    assert stages1 == stages2 == ["extract", "p1", "p2", "p3"]


def test_pmap_keeps_input_order_and_raises_first_error():
    assert pmap(lambda d: (time.sleep(d), d)[1], [0.05, 0.0, 0.02]) == [0.05, 0.0, 0.02]
    with pytest.raises(ValueError, match="второй"):
        pmap(lambda i: (_ for _ in ()).throw(ValueError("второй")) if i == 1 else i, range(3))


def test_review_waits_for_independent_group():
    class Review:
        NAME = "review"

        @staticmethod
        def run(client, document_text, requirements, findings=()):
            return []
    groups = pass_groups([timed_pass("a", 0, QUOTES[0]), Review, timed_pass("b", 0, QUOTES[1])])
    assert [[p.NAME for p in g] for g in groups] == [["a"], ["review"], ["b"]]


def test_concurrency_setting(monkeypatch, tmp_path):
    from agent.config import ConfigError, load_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.delenv("AGENT_CONCURRENCY", raising=False)
    assert load_settings().concurrency == 3
    monkeypatch.setenv("AGENT_CONCURRENCY", "1")
    assert load_settings().concurrency == 1
    monkeypatch.setenv("AGENT_CONCURRENCY", "0")
    with pytest.raises(ConfigError):
        load_settings()

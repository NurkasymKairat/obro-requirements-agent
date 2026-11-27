"""Общий интерфейс клиента модели и обёртка: кеш, пауза под RPM, повторы, запасные модели, учёт токенов.

Цепочка моделей: основная, затем запасные. На текущей модели — до switch_after повторов с нарастающей
паузой при 429/5xx/сбое сети, затем переход к следующей модели. Переход окончательный до конца прогона.
Модель недоступна (нет такой модели, дневная квота) — переход сразу. Закончился баланс аккаунта (402) —
остановка без перехода: баланс общий для всех моделей. Так же — если проекту Google недоступен
бесплатный тариф (403): ошибка относится ко всему проекту.

Тайм-аут запроса зависит от прохода: AGENT_TIMEOUT (90 с) для проходов с минимальным рассуждением,
AGENT_TIMEOUT_THINKING (360 с) для проходов с THINKING medium или high. Зависание (истёкший тайм-аут, 504)
отличается от быстрого отказа (503, 429, обрыв сети): после зависания — один повтор на той же модели без
паузы, затем сразу следующая модель; три повтора остаются для быстрых отказов.

Кеш: ответ берётся только от той модели, которая сейчас запрошена. Ключ — провайдер, модель, параметры
генерации, промпт, вход и схема. Прерванный прогон продолжается с последнего успешного вызова той же модели.

Учёт: на каждый вызов API пишется запись (стадия, модель, время, токены входа, выхода и рассуждения).
Токены учитываются и у ответа, который не прошёл схему или обрезан: за них тоже платим.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .cache import DiskCache

T = TypeVar("T", bound=BaseModel)

SWITCH_AFTER = 3          # повторов на одной модели до перехода к следующей (быстрые отказы)
HANG_RETRIES = 1          # повторов на одной модели после зависания (тайм-аут): ждать дольше слишком дорого
DEFAULT_CONCURRENCY = 3   # одновременных вызовов API (AGENT_CONCURRENCY)
MAX_SINGLE_WAIT = 120.0   # дольше ждать не имеет смысла: переходим к следующей модели
TOKEN_KEYS = ("tokens_in", "tokens_out", "tokens_thinking")
# Уровни рассуждения прохода. off и low — минимальный доступный уровень модели; on — прежний синоним high.
THINKING_MODES = ("off", "low", "medium", "high", "on")
DEFAULT_TIMEOUT = 90.0             # тайм-аут запроса, с: проходы с минимальным рассуждением (AGENT_TIMEOUT)
DEFAULT_TIMEOUT_THINKING = 360.0   # проходы с THINKING medium или high (AGENT_TIMEOUT_THINKING)


class LLMClient(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, schema: type[T], stage: str) -> T: ...


class LLMError(RuntimeError):
    pass


class AllModelsUnavailable(LLMError):
    """Ни одна модель цепочки не ответила. Готовые ответы остались в кеше."""


class BalanceExhausted(LLMError):
    """Закончился баланс аккаунта провайдера (402). Переход на другую модель не поможет."""


class FreeTierUnavailable(BalanceExhausted):
    """Проекту Google недоступен бесплатный тариф Gemini API (403). Ошибка относится ко всему проекту:
    повторы и запасные модели не помогут. Наследует BalanceExhausted, чтобы прогон останавливался так же."""

    exit_code = 6


# Совместимость: прежнее имя исключения «квота исчерпана».
QuotaExhausted = AllModelsUnavailable


class RetryableError(Exception):
    """Временный сбой: 429, 5xx, сеть. hang — запрос завис до тайм-аута, а не получил быстрый отказ."""

    def __init__(self, msg: str, retry_after: float | None = None, hang: bool = False) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.hang = hang


class ModelUnavailable(Exception):
    """Модель недоступна для этого ключа: нет такой модели, исчерпана дневная квота."""


class BadOutput(Exception):
    """Ответ не разобрался по схеме или обрезан."""


@dataclass
class CallResult:
    """Ответ API на один вызов. problem != None — ответ получен (и оплачен), но непригоден."""
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    problem: str | None = None
    thinking_requested: bool = False   # клиент запросил заметное рассуждение (medium, high)


def _env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = -1.0
    if value <= 0:
        from ..config import ConfigError
        raise ConfigError(f"{name} должно быть положительным числом секунд, сейчас: {raw!r}")
    return value


def timeout_error(seconds: float) -> RetryableError:
    return RetryableError(f"тайм-аут запроса, {seconds:.0f} с без ответа", 0.0, hang=True)


def server_timeout_error(code: int) -> RetryableError:
    return RetryableError(f"{code}, тайм-аут на стороне сервиса", 0.0, hang=True)


def _new_stats() -> dict:
    return {"calls": 0, "cache_hits": 0, "retries": 0, "models": {}, "tokens_in": 0, "tokens_out": 0,
            "tokens_thinking": 0, "thinking_zero": 0, "call_log": []}


@dataclass
class _Budget:
    calls: int = 0
    tokens: dict = field(default_factory=lambda: dict.fromkeys(TOKEN_KEYS, 0))


class BaseLLMClient:
    """Наследник реализует _call(model, system, user, schema) -> CallResult и gen_params.

    Потокобезопасен: проходы и пачки вызывают complete() из нескольких потоков. Одновременно к API идут
    не больше concurrency вызовов (семафор); пауза под RPM выдаётся слотами под блокировкой; ожидание перед
    повтором идёт вне семафора, чтобы остальные вызовы продолжали работу. Параметры прохода (уровень
    рассуждения, лимит выхода) хранятся в контекстных переменных: у параллельных проходов они свои.
    """

    name = "base"

    def __init__(self, models: list[str], rpm: float, cache: DiskCache, switch_after: int = SWITCH_AFTER,
                 concurrency: int = DEFAULT_CONCURRENCY) -> None:
        chain: list[str] = []
        for m in models:
            if m and m not in chain:
                chain.append(m)
        if not chain:
            raise LLMError("Не задана модель")
        self.models = chain
        self._idx = 0
        self.min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self.cache = cache
        self.switch_after = switch_after
        self.concurrency = max(1, int(concurrency))
        self._sem = threading.BoundedSemaphore(self.concurrency)
        self._lock = threading.RLock()
        self._next_slot = 0.0
        self._fails: dict[str, int] = defaultdict(int)   # подряд идущие сбои модели — общие для всех вызовов
        self._hangs: dict[str, int] = defaultdict(int)   # из них зависания (тайм-ауты)
        self.stats: dict[str, dict] = defaultdict(_new_stats)
        self.switches: list[dict] = []   # {"stage","from","to","reason"}
        self.total = _Budget()
        self._thinking: ContextVar[str] = ContextVar(f"thinking_{id(self)}", default="on")
        self._max_out: ContextVar[int | None] = ContextVar(f"max_out_{id(self)}", default=None)
        self.timeout_plain = _env_seconds("AGENT_TIMEOUT", DEFAULT_TIMEOUT)
        self.timeout_thinking = _env_seconds("AGENT_TIMEOUT_THINKING", DEFAULT_TIMEOUT_THINKING)

    # --- параметры текущего прохода (свои у каждого потока/контекста) ---
    @property
    def thinking(self) -> str:
        """Уровень рассуждения текущего прохода: off | low | medium | high (on — синоним high)."""
        return self._thinking.get()

    @thinking.setter
    def thinking(self, value: str) -> None:
        self._thinking.set(value)

    @property
    def max_output_tokens_override(self) -> int | None:
        return self._max_out.get()

    @property
    def max_output_tokens_effective(self) -> int | None:
        return self._max_out.get() or getattr(self, "max_output_tokens", None)

    @contextmanager
    def using_pass(self, thinking: str | None = None, max_output_tokens: int | None = None):
        """Параметры прохода на время его вызовов: уровень рассуждения и лимит выхода.
        Интерфейс complete() не меняется; оба значения входят в ключ кеша через gen_params()."""
        if thinking is not None and thinking not in THINKING_MODES:
            raise LLMError(f"THINKING={thinking!r}: допустимо off, low, medium, high")
        if max_output_tokens is not None and int(max_output_tokens) <= 0:
            raise LLMError(f"MAX_OUTPUT_TOKENS={max_output_tokens!r}: нужно положительное число")
        t1 = self._thinking.set(thinking) if thinking is not None else None
        t2 = self._max_out.set(int(max_output_tokens)) if max_output_tokens is not None else None
        try:
            yield
        finally:
            if t2 is not None:
                self._max_out.reset(t2)
            if t1 is not None:
                self._thinking.reset(t1)

    def request_timeout(self, model: str | None = None) -> float:
        """Тайм-аут одного запроса, с: зависит от уровня рассуждения текущего прохода."""
        return self.timeout_thinking if self.thinking in ("medium", "high", "on") else self.timeout_plain

    def using_thinking(self, mode: str | None):
        """Совместимость: только уровень рассуждения."""
        return self.using_pass(thinking=mode)

    @property
    def model(self) -> str:
        return self.models[self._idx]

    # --- для наследников ---
    def gen_params(self) -> dict:
        return {}

    def _call(self, model: str, system: str, user: str, schema: type[BaseModel]) -> CallResult:
        raise NotImplementedError

    # --- общий код ---
    def _key(self, model: str, system: str, user: str, schema: type[BaseModel]) -> str:
        return self.cache.key(provider=self.name, model=model, params=self.gen_params(),
                              system=system, user=user, schema=schema.model_json_schema())

    def _from_cache(self, system: str, user: str, schema: type[T], stage: str) -> T | None:
        """Только запрошенная (текущая) модель: ответы других моделей не подмешиваются."""
        model = self.model
        cached = self.cache.get(self._key(model, system, user, schema))
        if cached is None:
            return None
        try:
            obj = schema.model_validate(cached["response"])
        except (KeyError, ValidationError):
            return None
        with self._lock:
            st = self.stats[stage]
            st["cache_hits"] += 1
            st["models"][model] = st["models"].get(model, 0) + 1
        _log(f"  [{stage}] ответ из кеша ({model})")
        return obj

    def _switch(self, stage: str, reason: str, failed_model: str) -> None:
        """Переход на следующую модель — общий для всех вызовов. Если другой вызов уже перешёл
        с failed_model, этот просто продолжает на новой модели без своего перехода."""
        with self._lock:
            if self.model != failed_model:
                return
            if self._idx + 1 >= len(self.models):
                raise AllModelsUnavailable(
                    f"Все модели недоступны ({', '.join(self.models)}). Последняя причина: {reason}. "
                    "Обычно это временная перегрузка сервиса (503) или исчерпанный лимит тарифа. "
                    "Запустите ту же команду позже: готовые ответы возьмутся из кеша. "
                    "Другую модель можно задать в .env, см. README.")
            self._idx += 1
            self.switches.append({"stage": stage, "from": failed_model, "to": self.model, "reason": reason})
            new = self.model
        _log(f"  [{stage}] модель {failed_model} недоступна ({reason}); переход на {new} для всех следующих вызовов")

    def _account(self, stage: str, n: int, model: str, res: CallResult | None, seconds: float, status: str) -> None:
        entry = {"n": n, "model": model, "seconds": round(seconds, 1), "status": status,
                 "thinking": self.thinking, "max_output_tokens": self.max_output_tokens_effective,
                 "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0}
        zero = False
        with self._lock:
            st = self.stats[stage]
            if res is not None:
                for k in TOKEN_KEYS:
                    v = int(getattr(res, k) or 0)
                    entry[k] = v
                    st[k] += v
                    self.total.tokens[k] += v
                if res.thinking_requested and not entry["tokens_thinking"]:
                    st["thinking_zero"] += 1
                    zero = True
            st["call_log"].append(entry)
        tok = (f"токены: вход {entry['tokens_in']}, выход {entry['tokens_out']}, "
               f"рассуждение {entry['tokens_thinking']}") if res is not None else "без ответа"
        _log(f"  [{stage}] вызов {n} ← {model}: {status}, {seconds:.1f} с, {tok}")
        if zero:
            _log(f"  [{stage}] ВНИМАНИЕ: рассуждение запрошено, но по usage модель не рассуждала (0 токенов)")

    def _start_call(self, stage: str) -> int:
        with self._lock:
            self.stats[stage]["calls"] += 1
            self.total.calls += 1
            return self.total.calls

    def complete(self, system: str, user: str, schema: type[T], stage: str) -> T:
        hit = self._from_cache(system, user, schema, stage)
        if hit is not None:
            return hit

        attempt = 0          # повторы на текущей модели
        bad_output_left = 2
        tried: set[str] = set()
        while True:
            model = self.model
            # при переходе на другую модель её ответ мог уже лежать в кеше
            if tried and model not in tried and (hit := self._from_cache(system, user, schema, stage)) is not None:
                return hit
            tried.add(model)
            res: CallResult | None = None
            err: BaseException | None = None
            obj = None
            with self._sem:                       # не больше concurrency вызовов одновременно
                self._pace()
                n = self._start_call(stage)
                _log(f"  [{stage}] вызов {n} → {model}…")
                t0 = time.monotonic()
                try:
                    res = self._call(model, system, user, schema)
                    if res.problem:
                        raise BadOutput(res.problem)
                    obj = schema.model_validate_json(_strip_fences(res.text))
                except Exception as e:  # noqa: BLE001 — разбирается ниже, вне семафора
                    err = e
                seconds = time.monotonic() - t0

            if err is None:
                with self._lock:
                    self._fails[model] = 0
                    self._hangs[model] = 0
                self._account(stage, n, model, res, seconds, "ok")
                self.cache.put(self._key(model, system, user, schema),
                               {"stage": stage, "provider": self.name, "model": model,
                                "usage": {k: getattr(res, k) for k in TOKEN_KEYS},
                                "response": obj.model_dump(mode="json")})
                with self._lock:
                    st = self.stats[stage]
                    st["models"][model] = st["models"].get(model, 0) + 1
                return obj

            if isinstance(err, ModelUnavailable):
                self._account(stage, n, model, res, seconds, f"недоступна ({err})")
                self._switch(stage, str(err), model)
                attempt = 0
                continue
            if isinstance(err, RetryableError):
                self._account(stage, n, model, res, seconds, f"сбой ({err})")
                if self.model != model:           # другой вызов уже перешёл на запасную модель
                    attempt = 0
                    continue
                attempt += 1
                with self._lock:
                    self._fails[model] += 1
                    model_fails = self._fails[model]
                    self._hangs[model] += bool(err.hang)
                    hung_out = err.hang and self._hangs[model] > HANG_RETRIES
                wait = err.retry_after if err.retry_after is not None else 2.0 ** attempt
                # переход решает общий счётчик сбоев модели: при параллельных вызовах модель меняется
                # после switch_after повторов на всех вместе, а не на каждом вызове по отдельности
                # после зависания — один повтор и сразу следующая модель: каждая попытка стоит целый тайм-аут
                if hung_out or model_fails > self.switch_after or wait > MAX_SINGLE_WAIT:
                    self._switch(stage, str(err), model)
                    attempt = 0
                    continue
                with self._lock:
                    self.stats[stage]["retries"] += 1
                limit = HANG_RETRIES if err.hang else self.switch_after
                _log(f"  [{stage}] повтор {min(model_fails, limit)}/{limit} через {wait:.0f} с")
                time.sleep(wait)                  # вне семафора: остальные вызовы продолжают
                continue
            if isinstance(err, (BadOutput, ValidationError, json.JSONDecodeError)):
                self._account(stage, n, model, res, seconds, f"ответ не по схеме ({str(err)[:80]})")
                bad_output_left -= 1
                if bad_output_left < 0:
                    raise LLMError(f"[{stage}] модель {model} вернула ответ не по схеме: {str(err)[:300]}") from err
                with self._lock:
                    self.stats[stage]["retries"] += 1
                continue
            self._account(stage, n, model, res, seconds, "ошибка")
            raise err

    def _pace(self) -> None:
        """Слот под RPM: вызовы стартуют не чаще min_interval, в том числе из разных потоков."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        if slot > now:
            time.sleep(slot - now)


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

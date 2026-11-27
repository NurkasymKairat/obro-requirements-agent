"""Клиент Gemini (google-genai): строгий JSON по схеме, минимальная вариативность, учёт токенов.

Параметры генерации задаются здесь: temperature (0), seed, рассуждение, предел длины ответа.

Рассуждение задаётся режимом прохода (off | on, см. BaseLLMClient.using_thinking) через thinking_level:
on — "high", off — "low". Модели Gemini 3 не соблюдают thinking_budget (проверено по usage на
gemini-3.8-flash: бюджеты 0 и 2048 дают то же рассуждение, что "low"), а уровень "minimal" отвергают,
поэтому off — самый низкий доступный уровень, а не полное выключение. GEMINI_THINKING_BUDGET работает как
потолок: 0 переводит все проходы в off. Если модель отвергает seed или параметр рассуждения (400),
параметр снимается для этой модели и запоминается до конца прогона.

Тайм-аут задаётся на каждый запрос по уровню рассуждения прохода (BaseLLMClient.request_timeout).
"""
from __future__ import annotations

import copy
import json
import re

from pydantic import BaseModel

from .base import (BadOutput, BalanceExhausted, BaseLLMClient, CallResult, FreeTierUnavailable, LLMError,
                   ModelUnavailable, RetryableError, server_timeout_error, timeout_error)
from .cache import DiskCache

BALANCE_MSG = ("Gemini 402: закончился баланс платного аккаунта. Пополните баланс в Google AI Studio "
               "(раздел Billing) и запустите ту же команду: готовые ответы возьмутся из кеша.")
FREE_TIER_MSG = ("Gemini 403: в этом проекте Google недоступен бесплатный тариф Gemini API. Создайте ключ "
                 "в новом проекте AI Studio (https://aistudio.google.com/apikey) под личным аккаунтом Google "
                 "либо включите оплату в этом проекте, затем впишите ключ в .env и запустите ту же команду.")


# Уровень прохода -> thinking_level API. off и low — минимальный уровень, который принимает Gemini 3
# ("minimal" модель отвергает). "on" — прежний синоним high (его подменяет eval/apply_review.py).
THINKING_LEVELS = {"off": "low", "low": "low", "medium": "medium", "high": "high", "on": "high"}
LEVEL_ORDER = ("low", "medium", "high")


class GeminiClient(BaseLLMClient):
    name = "gemini"

    def __init__(self, api_key: str, models: list[str], rpm: float, cache: DiskCache, switch_after: int = 3,
                 temperature: float = 0.0, max_output_tokens: int = 32768, seed: int | None = 42,
                 timeout_s: float = 240.0, thinking_budget: int | None = 2048, _client=None,
                 concurrency: int = 3) -> None:
        super().__init__(models, rpm, cache, switch_after, concurrency)
        if _client is None:
            from google import genai
            from google.genai import types

            # без таймаута зависшее соединение держит прогон бесконечно
            _client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        self._client = _client
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.seed = seed
        self.thinking_budget = thinking_budget
        self._rejected: dict[str, set[str]] = {}   # модель -> параметры, которые она не принимает
        self._rejected_levels: dict[str, set[str]] = {}   # модель -> уровни рассуждения, которые она отвергла

    def thinking_level(self, model: str | None = None) -> str:
        """Уровень для запроса: режим прохода, потолок GEMINI_THINKING_BUDGET=0 и уровни, отвергнутые моделью
        (отвергнутый уровень заменяется ближайшим нижним)."""
        level = THINKING_LEVELS["off" if self.thinking_budget == 0 else self.thinking]
        bad = self._rejected_levels.get(model or self.model, set())
        while level in bad and level in LEVEL_ORDER and LEVEL_ORDER.index(level) > 0:
            level = LEVEL_ORDER[LEVEL_ORDER.index(level) - 1]
        return level

    def request_timeout(self, model: str | None = None) -> float:
        """По уровню, который реально уйдёт в запрос (с учётом потолка и отвергнутых уровней)."""
        return self.timeout_thinking if self.thinking_level(model) in ("medium", "high") else self.timeout_plain

    def gen_params(self) -> dict:
        return {"temperature": self.temperature, "seed": self.seed,
                "max_output_tokens": self.max_output_tokens_effective, "thinking_level": self.thinking_level()}

    def _config(self, model: str, system: str, schema: type[BaseModel]):
        from google.genai import types

        rejected = self._rejected.get(model, set())
        kw = dict(system_instruction=system, response_mime_type="application/json",
                  response_json_schema=inline_refs(schema.model_json_schema()),
                  temperature=self.temperature, max_output_tokens=self.max_output_tokens_effective,
                  http_options=types.HttpOptions(timeout=int(self.request_timeout(model) * 1000)))
        if self.seed is not None and "seed" not in rejected:
            kw["seed"] = self.seed
        if "thinking" not in rejected:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=self.thinking_level(model))
        return types.GenerateContentConfig(**kw)

    def _call(self, model: str, system: str, user: str, schema: type[BaseModel]) -> CallResult:
        from google.genai import errors

        try:
            resp = self._client.models.generate_content(model=model, contents=user,
                                                        config=self._config(model, system, schema))
        except errors.APIError as e:
            code = getattr(e, "code", None) or 0
            details = getattr(e, "details", None) or getattr(e, "response_json", None)
            text = f"{getattr(e, 'message', '') or ''} {json.dumps(details, default=str, ensure_ascii=False)}".lower()
            if code == 402:
                raise BalanceExhausted(BALANCE_MSG) from e
            if code == 403 and "free tier is not available" in text:
                raise FreeTierUnavailable(FREE_TIER_MSG) from e
            if code == 429:
                if _is_daily_quota(details):
                    raise ModelUnavailable("429, исчерпана дневная квота") from e
                raise RetryableError("429, лимит запросов в минуту", _retry_delay(details)) from e
            if code in (408, 504):
                raise server_timeout_error(code) from e
            if code >= 500:
                raise RetryableError(f"{code}, сервис перегружен или недоступен") from e
            if code == 404:
                raise ModelUnavailable("404, модель не найдена для этого ключа") from e
            if code == 400:
                level = self.thinking_level(model)
                if "thinking" in text and "level" in text and level != "low" \
                        and level not in self._rejected_levels.get(model, set()):
                    # отвергнут уровень (например, medium): берём ближайший нижний, а не снимаем рассуждение
                    self._rejected_levels.setdefault(model, set()).add(level)
                    raise RetryableError(f"400, модель не принимает уровень {level}; повтор с нижним", 0.0) from e
                param = _rejected_param(text)
                if param and param not in self._rejected.get(model, set()):
                    self._rejected.setdefault(model, set()).add(param)
                    raise RetryableError(f"400, модель не принимает {param}; повтор без него", 0.0) from e
            raise LLMError(f"Gemini {code}: {str(e)[:500]}") from e
        except TimeoutError as e:
            raise timeout_error(self.request_timeout(model)) from e
        except OSError as e:
            raise RetryableError(f"сеть: {e}") from e
        except Exception as e:  # httpx и прочие транспортные ошибки
            if type(e).__module__.startswith(("httpx", "httpcore")):
                if "Timeout" in type(e).__name__:
                    raise timeout_error(self.request_timeout(model)) from e
                raise RetryableError(f"сеть: {type(e).__name__}") from e
            raise

        u = getattr(resp, "usage_metadata", None)
        rejected = self._rejected.get(model, set())
        res = CallResult(text=resp.text or "",
                         thinking_requested=self.thinking_level(model) in ("medium", "high") and "thinking" not in rejected,
                         tokens_in=getattr(u, "prompt_token_count", 0) or 0,
                         tokens_out=getattr(u, "candidates_token_count", 0) or 0,
                         tokens_thinking=getattr(u, "thoughts_token_count", 0) or 0)
        cand = (resp.candidates or [None])[0]
        finish = str(getattr(cand, "finish_reason", "") or "")
        if "MAX_TOKENS" in finish:
            res.problem = "ответ обрезан по max_output_tokens"
        elif not res.text:
            res.problem = f"пустой ответ (finish_reason={finish or 'нет'})"
        return res


def _rejected_param(text: str) -> str | None:
    """Какой необязательный параметр отверг API в ответе 400."""
    if "thinking" in text:
        return "thinking"
    if "seed" in text:
        return "seed"
    return None


def inline_refs(schema: dict) -> dict:
    """Разворачивает $defs/$ref: так схема принимается API без оговорок."""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged)
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _find(obj, key: str):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _find(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find(v, key)


def _retry_delay(details) -> float | None:
    for v in _find(details, "retryDelay"):
        m = re.match(r"([\d.]+)s", str(v))
        if m:
            return float(m.group(1)) + 1.0
    return None


def _is_daily_quota(details) -> bool:
    blob = json.dumps(details, default=str) if details is not None else ""
    return "PerDay" in blob

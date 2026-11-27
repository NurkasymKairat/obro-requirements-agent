"""Клиент OpenAI (Responses API): строгий JSON по pydantic-схеме.

Параметры генерации задаются здесь, общий код их не знает. У части моделей GPT-6 нет temperature,
только reasoning.effort, поэтому temperature не передаётся никогда. Если модель не принимает и
reasoning, параметр снимается после первого отказа 400 и запоминается для этой модели.
Повторы делает BaseLLMClient: встроенные повторы SDK выключены (max_retries=0).
Тайм-аут задаётся на каждый запрос по уровню рассуждения прохода (BaseLLMClient.request_timeout).
"""
from __future__ import annotations

from pydantic import BaseModel

from .base import (BalanceExhausted, BaseLLMClient, CallResult, LLMError, ModelUnavailable, RetryableError,
                   server_timeout_error, timeout_error)
from .cache import DiskCache


BALANCE_MSG = ("OpenAI: закончился баланс или квота аккаунта (402 / insufficient_quota). Пополните баланс "
               "на platform.openai.com (Billing) и запустите ту же команду: готовые ответы возьмутся из кеша.")


class OpenAIClient(BaseLLMClient):
    name = "openai"

    def __init__(self, api_key: str, models: list[str], rpm: float, cache: DiskCache, switch_after: int = 3,
                 reasoning_effort: str = "medium", max_output_tokens: int = 32768, timeout_s: float = 240.0,
                 http_client=None, base_url: str | None = None, concurrency: int = 3) -> None:
        super().__init__(models, rpm, cache, switch_after, concurrency)
        import openai

        kw = {"api_key": api_key, "timeout": timeout_s, "max_retries": 0}
        if http_client is not None:
            kw["http_client"] = http_client
        if base_url:
            kw["base_url"] = base_url
        self._client = openai.OpenAI(**kw)
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self._no_reasoning: set[str] = set()

    def effort(self) -> str:
        """Уровень прохода -> reasoning.effort: off и low — "low", medium и high — как есть,
        on — настроенный OPENAI_REASONING_EFFORT."""
        if self.thinking == "on":
            return self.reasoning_effort
        return {"off": "low", "low": "low", "medium": "medium", "high": "high"}[self.thinking]

    def gen_params(self) -> dict:
        return {"reasoning_effort": self.effort(), "max_output_tokens": self.max_output_tokens_effective}

    def _request_params(self, model: str) -> dict:
        p: dict = {"max_output_tokens": self.max_output_tokens_effective, "store": False}
        if self.reasoning_effort and model not in self._no_reasoning:
            p["reasoning"] = {"effort": self.effort()}
        return p

    def _call(self, model: str, system: str, user: str, schema: type[BaseModel]) -> CallResult:
        import openai
        from openai.lib._pydantic import to_strict_json_schema

        text_format = {"format": {"type": "json_schema", "name": schema.__name__[:64],
                                  "schema": to_strict_json_schema(schema), "strict": True}}
        try:
            resp = self._client.responses.create(model=model, instructions=system, input=user,
                                                 text=text_format, timeout=self.request_timeout(model),
                                                 **self._request_params(model))
        except openai.RateLimitError as e:
            if _error_code(e) == "insufficient_quota":
                raise BalanceExhausted(BALANCE_MSG) from e
            raise RetryableError("429, лимит запросов", _retry_after(e)) from e
        except (openai.NotFoundError, openai.PermissionDeniedError) as e:
            raise ModelUnavailable(f"{e.status_code}, модель недоступна для этого ключа") from e
        except openai.AuthenticationError as e:
            raise LLMError("OpenAI 401: ключ не принят. Проверьте OPENAI_API_KEY в .env.") from e
        except openai.BadRequestError as e:
            if _error_param(e) == "reasoning" or ("reasoning" in str(e).lower() and "unsupported" in str(e).lower()):
                self._no_reasoning.add(model)
                raise RetryableError("400, модель не принимает reasoning; повтор без него", 0.0) from e
            raise LLMError(f"OpenAI 400: {str(e)[:500]}") from e
        except openai.APIStatusError as e:
            if e.status_code == 402:
                raise BalanceExhausted(BALANCE_MSG) from e
            if e.status_code in (408, 504):
                raise server_timeout_error(e.status_code) from e
            if e.status_code >= 500 or e.status_code == 409:
                raise RetryableError(f"{e.status_code}, сервис перегружен или недоступен") from e
            raise LLMError(f"OpenAI {e.status_code}: {str(e)[:500]}") from e
        except openai.APITimeoutError as e:
            raise timeout_error(self.request_timeout(model)) from e
        except openai.APIConnectionError as e:
            raise RetryableError(f"сеть: {type(e).__name__}") from e

        u = getattr(resp, "usage", None)
        thinking = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
        res = CallResult(text=resp.output_text or "",
                         tokens_in=getattr(u, "input_tokens", 0) or 0,
                         # output_tokens у OpenAI включает рассуждение: показываем их раздельно
                         tokens_out=max((getattr(u, "output_tokens", 0) or 0) - thinking, 0),
                         tokens_thinking=thinking)
        if resp.status == "incomplete":
            reason = getattr(resp.incomplete_details, "reason", None) or "неизвестно"
            res.problem = f"ответ не завершён: {reason}"
            return res
        for item in resp.output or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", "") == "refusal":
                    res.problem = f"модель отказалась отвечать: {getattr(part, 'refusal', '')[:200]}"
                    return res
        if not res.text:
            res.problem = "пустой ответ"
        return res


def _body(e) -> dict:
    b = getattr(e, "body", None)
    if isinstance(b, dict):
        return b.get("error", b) if isinstance(b.get("error", b), dict) else b
    return {}


def _error_code(e) -> str | None:
    return _body(e).get("code")


def _error_param(e) -> str | None:
    p = _body(e).get("param")
    return p.split(".", 1)[0] if isinstance(p, str) else None


def _retry_after(e) -> float | None:
    resp = getattr(e, "response", None)
    v = resp.headers.get("retry-after") if resp is not None else None
    try:
        return float(v) + 1.0 if v else None
    except ValueError:
        return None

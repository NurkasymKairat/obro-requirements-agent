from __future__ import annotations

from ..config import Settings
from .base import (AllModelsUnavailable, BalanceExhausted, BaseLLMClient, FreeTierUnavailable, LLMClient, LLMError,
                   QuotaExhausted)
from .cache import DiskCache

__all__ = ["AllModelsUnavailable", "BalanceExhausted", "FreeTierUnavailable", "LLMClient", "LLMError", "QuotaExhausted", "make_client"]


def make_client(s: Settings) -> BaseLLMClient:
    cache = DiskCache(s.cache_dir)
    if s.provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(s.gemini_api_key, [s.gemini_model, *s.gemini_fallback_models], s.gemini_rpm, cache,
                            s.switch_after,
                            temperature=s.gemini_temperature, max_output_tokens=s.gemini_max_output_tokens,
                            thinking_budget=s.gemini_thinking_budget,
                            concurrency=s.concurrency)
    from .openai import OpenAIClient

    return OpenAIClient(s.openai_api_key, [s.openai_model, *s.openai_fallback_models], s.openai_rpm, cache,
                        s.switch_after, reasoning_effort=s.openai_reasoning_effort,
                        max_output_tokens=s.openai_max_output_tokens,
                        concurrency=s.concurrency)

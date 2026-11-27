"""Настройки из .env и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_FALLBACKS = "gemini-3.6-flash,gemini-3.5-flash"
DEFAULT_GEMINI_THINKING_BUDGET = 2048   # токенов рассуждения на вызов; -1 — на усмотрение модели, 0 — выключено
DEFAULT_OPENAI_MODEL = "gpt-6-sol"
DEFAULT_OPENAI_FALLBACKS = "gpt-6-luna"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    provider: str
    gemini_api_key: str | None
    gemini_model: str
    gemini_fallback_models: list[str]
    gemini_rpm: float
    gemini_temperature: float
    gemini_max_output_tokens: int
    gemini_thinking_budget: int | None
    openai_api_key: str | None
    openai_model: str
    openai_fallback_models: list[str]
    openai_reasoning_effort: str
    openai_max_output_tokens: int
    openai_rpm: float
    cache_dir: Path
    switch_after: int
    concurrency: int


def _float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    try:
        return float(v) if v else default
    except ValueError as e:
        raise ConfigError(f"{name} должно быть числом, сейчас: {v!r}") from e


def _list(name: str, default: str) -> list[str]:
    raw = os.getenv(name)
    return [m.strip() for m in (default if raw is None else raw).split(",") if m.strip()]


def load_settings() -> Settings:
    load_dotenv(Path.cwd() / ".env", override=False)
    s = Settings(
        provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower() or "gemini",
        gemini_api_key=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None,
        gemini_model=os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL,
        gemini_fallback_models=_list("GEMINI_FALLBACK_MODELS", DEFAULT_GEMINI_FALLBACKS),
        gemini_rpm=_float("GEMINI_RPM", 10),
        gemini_temperature=_float("GEMINI_TEMPERATURE", 0),
        gemini_max_output_tokens=int(_float("GEMINI_MAX_OUTPUT_TOKENS", 32768)),
        gemini_thinking_budget=int(_float("GEMINI_THINKING_BUDGET", DEFAULT_GEMINI_THINKING_BUDGET)),
        openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
        openai_model=os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL,
        openai_fallback_models=_list("OPENAI_FALLBACK_MODELS", DEFAULT_OPENAI_FALLBACKS),
        openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "").strip().lower() or "medium",
        openai_max_output_tokens=int(_float("OPENAI_MAX_OUTPUT_TOKENS", 32768)),
        openai_rpm=_float("OPENAI_RPM", 60),
        cache_dir=Path(os.getenv("CACHE_DIR", "").strip() or ".cache/llm"),
        switch_after=int(_float("LLM_RETRIES_PER_MODEL", 3)),
        concurrency=int(_float("AGENT_CONCURRENCY", 3)),
    )
    if s.provider not in ("gemini", "openai"):
        raise ConfigError(f"LLM_PROVIDER={s.provider!r}: допустимо gemini или openai")
    if s.provider == "gemini" and not s.gemini_api_key:
        raise ConfigError("Не задан GEMINI_API_KEY. Получите ключ в Google AI Studio и положите его "
                          "в файл .env в корне проекта (см. README, раздел «Ключ»).")
    if s.gemini_thinking_budget is not None and s.gemini_thinking_budget < -1:
        raise ConfigError("GEMINI_THINKING_BUDGET: число токенов ≥ 0 или -1 (на усмотрение модели)")
    if s.concurrency < 1:
        raise ConfigError("AGENT_CONCURRENCY: число одновременных вызовов, не меньше 1")
    if s.openai_reasoning_effort not in REASONING_EFFORTS:
        raise ConfigError(f"OPENAI_REASONING_EFFORT={s.openai_reasoning_effort!r}: допустимо {', '.join(REASONING_EFFORTS)}")
    if s.provider == "openai" and not s.openai_api_key:
        raise ConfigError("Не задан OPENAI_API_KEY в .env (LLM_PROVIDER=openai).")
    return s

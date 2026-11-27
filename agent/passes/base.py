"""Общее для проходов анализа: загрузка промпта, схема ответа модели, вызов.

Файл промпта prompts/<имя>.md: системная часть, строка `---USER---`, пользовательская часть.
Подстановки jinja2: {{ common }} (prompts/_common.md), {{ document }}, {{ requirements }}, {{ batch }}.
Проход возвращает list[RawFinding]: цитаты сверяет и превращает в Finding ядро (verify).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, Field

from agent.schemas import RawFinding, Requirement

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
USER_MARK = "---USER---"
_env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


class FindingsOutput(BaseModel):
    findings: list[RawFinding] = Field(description="Замечания; пустой список, если дефектов нет")


class Client(Protocol):
    def complete(self, system: str, user: str, schema: type[BaseModel], stage: str) -> BaseModel: ...


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def load_prompt(name: str, **values: str) -> tuple[str, str]:
    """Вернуть (system, user) для прохода name с подставленными значениями."""
    raw = _read(name)
    if USER_MARK not in raw:
        raise ValueError(f"В промпте {name}.md нет разделителя {USER_MARK}")
    system, user = raw.split(USER_MARK, 1)
    system = _env.from_string(system).render(common=_read("_common").strip())
    user = _env.from_string(user).render(**values)
    return system.strip(), user.strip()


_BLOCK_MARK = re.compile(r"^\[(B-\d+)\] ?", re.M)


def block_texts(document_text: str) -> dict[str, str]:
    """Текст каждого блока из документа с метками [B-012]."""
    parts = _BLOCK_MARK.split(document_text)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def render_requirements(requirements: Sequence[Requirement], document_text: str = "") -> str:
    """Список требований для модели. С document_text — полный текст блока требования:
    цитата из нарезки бывает обрезана, и модель принимает обрыв за дефект документа.
    Без document_text — краткий пересказ (для справочного списка)."""
    blocks = block_texts(document_text) if document_text else {}
    lines = []
    for r in requirements:
        source = f" (в документе: {r.source_id})" if r.source_id else " (без номера в документе)"
        text = blocks.get(r.quote.block_id) if blocks else r.summary
        lines.append(f"- {r.rid}{source}, блок {r.quote.block_id}, раздел «{r.section}»: {text or r.quote.text}")
    return "\n".join(lines) if lines else "(требования не выделены)"


def ask(client: Client, name: str, stage: str | None = None, **values: str) -> list[RawFinding]:
    system, user = load_prompt(name, **values)
    result = client.complete(system, user, FindingsOutput, stage or name)
    return list(result.findings)

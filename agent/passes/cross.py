"""Объединённая перекрёстная сверка одним вызовом: критерии приёмки и части документа."""
from __future__ import annotations

from collections.abc import Sequence

from agent.schemas import RawFinding, Requirement

from .base import Client, ask, render_requirements

NAME = "cross"


def run(client: Client, document_text: str, requirements: Sequence[Requirement]) -> list[RawFinding]:
    return ask(client, NAME, document=document_text, requirements=render_requirements(requirements, document_text))

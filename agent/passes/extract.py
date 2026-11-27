"""Проход 1 (ядро): нарезка документа на требования с дословными цитатами.

Интерфейс отличается от проходов анализа: run(client, document_text) -> list[RawRequirement].
Промпт prompts/extract.md — только системная часть; документ передаётся пользовательским сообщением.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from agent.schemas import RawRequirement

from .base import PROMPTS_DIR, Client

NAME = "extract"
THINKING = "off"   # нарезка — механическая задача, рассуждение не окупается


class ExtractResponse(BaseModel):
    requirements: list[RawRequirement] = Field(description="Все требования документа в порядке следования")


def run(client: Client, document_text: str) -> list[RawRequirement]:
    system = (PROMPTS_DIR / f"{NAME}.md").read_text(encoding="utf-8").strip()
    user = ("Документ. Каждый блок начинается с метки [B-NNN]; метка не входит в текст блока.\n\n"
            f"{document_text}")
    resp = client.complete(system, user, ExtractResponse, NAME)
    return list(resp.requirements)

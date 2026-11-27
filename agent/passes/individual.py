"""Проход 2: каждое требование по девяти свойствам ISO 29148 п. 5.2.5, пачками."""
from __future__ import annotations

import math
import os
from collections.abc import Sequence

from agent.parallel import pmap
from agent.schemas import RawFinding, Requirement

from .base import Client, ask, render_requirements

NAME = "individual"
# Наибольший размер пачки. Требования делятся на равные пачки не больше этого размера.
MAX_BATCH = int(os.getenv("INDIVIDUAL_MAX_BATCH", "16"))


def batches(requirements: Sequence[Requirement], max_batch: int = 0) -> list[Sequence[Requirement]]:
    size = max_batch or MAX_BATCH
    n = len(requirements)
    count = max(1, math.ceil(n / size))
    per = math.ceil(n / count) if n else 0
    return [requirements[i:i + per] for i in range(0, n, per)] if n else []


def run(client: Client, document_text: str, requirements: Sequence[Requirement]) -> list[RawFinding]:
    """document_text — документ с метками блоков (reader.render_for_llm)."""
    all_reqs = render_requirements(requirements)
    parts = batches(requirements)
    # пачки независимы: идут параллельно (ограничение — AGENT_CONCURRENCY в клиенте), результаты — в порядке пачек;
    # stage = имя прохода: по нему ядро считает вызовы; пачки различает кеш по тексту запроса
    found_by_batch = pmap(lambda batch: ask(client, NAME, document=document_text, requirements=all_reqs,
                                            batch=render_requirements(batch, document_text)), parts)
    out: list[RawFinding] = []
    for batch, found in zip(parts, found_by_batch):
        allowed = {r.rid for r in batch}
        for f in found:
            f.scope = "requirement"
            f.requirement_ids = [rid for rid in f.requirement_ids if rid in allowed]
            out.append(f)
    return out

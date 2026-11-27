"""Параллельное выполнение независимых вызовов с детерминированным порядком результатов.

pmap(fn, items) запускает fn для каждого элемента в отдельном потоке и возвращает результаты в порядке
items, а не в порядке завершения. Число одновременных вызовов API ограничивает сам клиент модели
(AGENT_CONCURRENCY), здесь — только число потоков. Каждый поток получает копию контекста вызывающего,
поэтому параметры прохода (уровень рассуждения, лимит выхода) у пачек те же, что у прохода.
Первое исключение пробрасывается после завершения остальных: прогон не оставляет висящих потоков.
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")
MAX_THREADS = 8


def pmap(fn: Callable[[T], R], items: Iterable[T], max_threads: int = MAX_THREADS) -> list[R]:
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(len(items), max_threads)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, x) for x in items]
        errors = [f.exception() for f in futures]          # ждёт все, в порядке items
    for e in errors:
        if e is not None:
            raise e
    return [f.result() for f in futures]

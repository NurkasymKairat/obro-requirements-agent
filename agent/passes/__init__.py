"""Проходы анализа. Каждый модуль: NAME и run(client, document_text, requirements[, findings]).

Проход extract принадлежит ядру и в список не входит. Состав и порядок по умолчанию — DEFAULT;
для замеров его можно заменить переменной окружения AGENT_PASSES (имена через запятую).
"""
import os

from . import (acceptance_table, cross, cross_acceptance, cross_parts, individual, parts_goals, review,
               states_units)

# Не вошедшие в DEFAULT проходы — кандидаты: включаются только после замера eval/bench.py.
ALL = {m.NAME: m for m in (individual, cross_acceptance, cross_parts, acceptance_table, parts_goals, cross,
                           states_units, review)}
DEFAULT = ("individual", "cross_acceptance", "parts_goals", "review")


def _selected() -> tuple:
    names = [n.strip() for n in os.getenv("AGENT_PASSES", "").split(",") if n.strip()] or list(DEFAULT)
    unknown = [n for n in names if n not in ALL]
    if unknown:
        raise ValueError(f"AGENT_PASSES: неизвестные проходы {unknown}; доступны {sorted(ALL)}")
    return tuple(ALL[n] for n in names)


PASSES = _selected()

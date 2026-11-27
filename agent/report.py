"""Сборка Markdown-отчёта шаблоном. Модель даёт только содержимое полей."""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from .passes.calibrate import pick_main  # отбор для «Главного» — правила сессии B
from .pipeline import finding_sort_key, run_totals
from .schemas import RunResult

SEVERITY_RU = {"critical": "критично", "high": "высокая", "medium": "средняя", "low": "низкая"}
KIND_RU = {"ambiguous": "неоднозначность", "unverifiable": "непроверяемость", "contradiction": "противоречие",
           "incomplete": "неполнота", "design_in_requirement": "решение вместо требования", "other": "прочее"}
STATUS_RU = {"confirmed": "подтверждено", "potential": "потенциально"}
VERDICT_RU = {
    "ready": "Можно начинать разработку и тестирование.",
    "ready_with_fixes": "Можно начинать разработку после исправления дефектов высокой важности; "
                        "тестирование — после их исправления.",
    "not_ready": "Начинать разработку и тестирование нельзя: есть подтверждённые критичные дефекты.",
    "incomplete": "Вердикт не выносится: прогон неполный, часть проверок не выполнена.",
}
CRITICAL_KINDS = ("unverifiable", "contradiction", "incomplete")


def _cell(s: str | None) -> str:
    if not s:
        return "—"
    return " ".join(str(s).split()).replace("|", "\\|")


def _inline(s: str) -> str:
    return " ".join(s.split())


def _duration(sec) -> str:
    sec = float(sec or 0)
    return f"{sec:.1f} с" if sec < 60 else f"{int(sec // 60)} мин {int(sec % 60)} с"


STAGE_DEFAULTS = {"status": "ok", "thinking": None, "seconds": 0.0, "calls": 0, "retries": 0, "cache_hits": 0,
                  "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0, "thinking_zero": 0, "models": [],
                  "model_switches": [], "call_log": [], "items": 0, "dropped": [], "error": ""}


def _env() -> Environment:
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters.update(cell=_cell, inline=_inline, sev=SEVERITY_RU.get, kind=KIND_RU.get, status=STATUS_RU.get,
                       duration=_duration,
                       # функции оформления объявлены ниже; связываются при вызове
                       stage_ru=lambda x: stage_ru(x), calls_in=lambda n: calls_in(n),
                       headline=lambda t: headline(t), main_description=lambda t: main_description(t))
    return env


# Шаблон компилируется при импорте, вместе с кодом: правка файла шаблона посреди прогона
# не может рассинхронизировать уже запущенный процесс с шаблоном.
_TEMPLATE = _env().from_string((Path(__file__).parent / "templates" / "report.md.j2").read_text(encoding="utf-8"))


# ---------- служебные номера блоков (B-012) -> место в документе ----------

_BID = r"B-\d{3,}"
_BLOCK_WORD = r"(?:блок(?:ах|ами|ам|ом|е|и|а|у)?\s+)"
# ID, диапазон «B-034–B-037» или перечень «B-060, B-070 и B-074»
_IDS = rf"{_BID}(?:\s*[–—-]\s*{_BID})?(?:\s*(?:,|и)\s*{_BID}(?:\s*[–—-]\s*{_BID})?)*"
_REF_RE = re.compile(rf"(?:(?<![\w-])[вВ]\s+)?{_BLOCK_WORD}?(?<![\w-])(?P<ids>{_IDS})(?![\w-])", re.I)
_LINE_RE = re.compile(r"^(?P<sec>.*?),?\s*строк[аи]\s+(?P<n>\d+)$")
_SRC_SUFFIX_RE = re.compile(r"\s+\([^()]*\)$")


def _short_location(loc: str) -> str:
    """«Часть A › Критерии приёмки, строка 51» -> «Критерии приёмки, строка 51»."""
    return loc.split(" › ")[-1].strip()


def block_places(result: RunResult) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Место блока и номера требований в нём — из цитат самого результата (текст документа не нужен)."""
    places: dict[str, str] = {}
    sources: dict[str, set[str]] = {}
    for f in result.findings:
        for q in f.quotes:
            places.setdefault(q.block_id, _short_location(q.location))
    for r in result.requirements:
        q = r.quote
        loc = _SRC_SUFFIX_RE.sub("", q.location) if r.source_id else q.location
        places.setdefault(q.block_id, _short_location(loc))
        if r.source_id:
            sources.setdefault(q.block_id, set()).add(r.source_id)
    return places, sources


def _places_text(ids: str, places: dict[str, str]) -> str:
    parts = []
    for item in re.split(r"\s*(?:,|\sи\s)\s*", ids):
        rng = re.findall(_BID, item)
        if len(rng) == 2:
            a, b = places.get(rng[0]), places.get(rng[1])
            ma, mb = _LINE_RE.match(a or ""), _LINE_RE.match(b or "")
            if ma and mb and ma["sec"] == mb["sec"]:
                parts.append(f"{ma['sec']}, строки {ma['n']}–{mb['n']}")
            elif a and b:
                parts.append(f"{a} — {b}")
            elif a or b:
                parts.append(a or b)
        elif rng and places.get(rng[0]):
            parts.append(places[rng[0]])
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "; ".join(seen)


def humanize_refs(text: str, places: dict[str, str], sources: dict[str, set[str]] | None = None) -> str:
    """Служебные номера блоков в тексте модели -> человекочитаемое место «Раздел, строка N».

    «в блоке B-036» и «B-046» -> «(Критерии приёмки, строка 51)»; «(блок B-018)» -> «(…, строка 26)».
    Ссылка убирается, если место неизвестно или рядом уже названо требование из этого блока («П-3 (B-018)»).
    Номера требований (R-001) и пункты документа не трогаются.
    """
    if not text or "B-" not in text:
        return text
    sources = sources or {}

    def repl(m: re.Match) -> str:
        s, e = m.start(), m.end()
        wrapped = s > 0 and text[s - 1] == "(" and e < len(text) and text[e] == ")"
        ids = re.findall(_BID, m["ids"])
        before = text[max(0, s - 20):s].rstrip(" (")
        if any(before.endswith(src) for b in ids for src in sources.get(b, ())):
            return ""                                   # место уже названо номером требования
        place = _places_text(m["ids"], places)
        if not place:
            return ""
        if wrapped:
            return place
        if s > 0 and text[s - 1] == "(":
            return f"{place}:"                          # «(в блоке B-058 для …» -> «(Раздел, строка N: для …»
        if text.rfind("(", 0, s) > text.rfind(")", 0, s):
            return place                                # уже внутри скобок — без вложенных
        return f"({place})"

    out = _REF_RE.sub(repl, text)
    out = re.sub(r"\(\s*\)", "", out)                   # «П-3 ()» после удаления
    out = re.sub(r"\(\s*\(([^()]*)\)\s*\)", r"(\1)", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([,.;:)])", r"\1", out)
    return out.strip()


def humanize_result(result: RunResult) -> RunResult:
    """Копия результата, в текстах которой нет служебных номеров блоков. Цитаты и позиции не меняются."""
    places, sources = block_places(result)
    h = lambda s: humanize_refs(s, places, sources)  # noqa: E731
    findings = [f.model_copy(update={"description": h(f.description), "fix": h(f.fix)}) for f in result.findings]
    return result.model_copy(update={"findings": findings, "first_fixes": [h(x) for x in result.first_fixes]})


# ---------- оформление отчёта ----------

STAGE_RU = {
    "extract": "нарезка требований",
    "rules": "правила без модели",
    "individual": "проверка каждого требования",
    "cross_acceptance": "требования и критерии приёмки",
    "parts_goals": "цели и части документа",
    "review": "итоговая сверка",
    "cross_parts": "документ и спецификация API",
    "acceptance_table": "таблица критериев приёмки",
    "cross": "перекрёстная проверка",
    "states_units": "состояния, единицы и границы",
}
_MERGED_RE = re.compile(r"Сюда же относится \(F-\d+\)")
_MERGED_NOTE = "Сюда же относится (объединено с похожим замечанием)"
_SENTENCE_RE = re.compile(r"^(.+?[.!?])(?=\s+[А-ЯЁA-Z«\"'(]|$)", re.S)
HEADLINE_MAX = 120
MAIN_QUOTES_MAX = 3


def plural(n: int, one: str, few: str, many: str) -> str:
    a = abs(n) % 100
    if 10 < a < 20:
        return many
    return one if a % 10 == 1 else few if 2 <= a % 10 <= 4 else many


def stage_ru(name: str) -> str:
    """«нарезка требований (extract)»; неизвестный проход — внутреннее имя."""
    ru = STAGE_RU.get(name)
    return f"{ru} ({name})" if ru else name


def calls_in(n: int) -> str:
    """«в 1 вызове», «в 3 вызовах» (предложный падеж)."""
    return f"в {n} {'вызове' if n % 10 == 1 and n % 100 != 11 else 'вызовах'}"


def main_description(text: str) -> str:
    """Описание основного замечания без вставок слитых («Сюда же относится …»)."""
    i = text.find(" Сюда же относится (")
    return (text[:i] if i >= 0 else text).strip()


def headline(text: str, limit: int = HEADLINE_MAX) -> str:
    """Заголовок дефекта: первое предложение основного описания целиком;
    длиннее limit — обрезка по границе слова с многоточием «…» в конце."""
    t = " ".join(main_description(text).split())
    m = _SENTENCE_RE.match(t)
    first = (m.group(1) if m else t).rstrip()
    if len(first) <= limit:
        return first
    cut = first[:limit + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else first[:limit]
    return cut.rstrip(" ,;:—-") + "…"


def dedupe_quotes(quotes: list) -> list:
    """Одинаковые и вложенные цитаты из одного блока — один раз, остаётся более полная."""
    keep = []
    for i, q in enumerate(quotes):
        covered = any(
            j != i and o.block_id == q.block_id and o.start <= q.start and q.end <= o.end
            and ((o.end - o.start) > (q.end - q.start) or j < i)
            for j, o in enumerate(quotes))
        if not covered:
            keep.append(q)
    return keep


def polish_for_report(result: RunResult) -> RunResult:
    """Только для Markdown-отчёта: номера слитых замечаний -> слова, без повторов цитат."""
    findings = [f.model_copy(update={"description": _MERGED_RE.sub(_MERGED_NOTE, f.description),
                                     "quotes": dedupe_quotes(f.quotes)}) for f in result.findings]
    return result.model_copy(update={"findings": findings})


def summary_line(result: RunResult, confirmed: list) -> str:
    """«Итог: начинать разработку и тестирование нельзя — 3 критичных дефекта» (текст из VERDICT_RU)."""
    head = re.split(r"[:;]", VERDICT_RU[result.verdict])[0].strip().rstrip(".")
    head = head[:1].lower() + head[1:]
    tail = ""
    if result.verdict == "not_ready":
        n = sum(1 for f in confirmed if f.severity == "critical")
        tail = f" — {n} {plural(n, 'критичный дефект', 'критичных дефекта', 'критичных дефектов')}"
    elif result.verdict == "ready_with_fixes":
        n = sum(1 for f in confirmed if f.severity == "high")
        tail = f" — {n} {plural(n, 'дефект', 'дефекта', 'дефектов')} высокой важности"
    return f"Итог: {head}{tail}"


def review_notes(stages: list[dict]) -> tuple[dict, dict]:
    """Из журнала ревью: какие замечания слиты в какое и почему оспорены."""
    merged: dict[str, list] = {}
    reasons: dict[str, str] = {}
    for s in stages:
        rlog = s.get("review_log") or {}
        for m in rlog.get("merged", []):
            merged.setdefault(m["into"], []).append(m)
        for d in rlog.get("disputed", []):
            reasons[d["fid"]] = d.get("reason", "")
        for fid in rlog.get("one_sided", []):
            reasons.setdefault(fid, "противоречие процитировано только с одной стороны")
    return merged, reasons


def normalize_stages(stages: list[dict]) -> list[dict]:
    """Стадии с полным набором полей: шаблон собирается и из report.json прежних версий."""
    return [{**STAGE_DEFAULTS, **s} for s in stages]


def render(result: RunResult) -> str:
    result = polish_for_report(humanize_result(result.model_copy(update={"stages": normalize_stages(result.stages)})))
    findings = sorted(result.findings, key=finding_sort_key)
    # Основные разделы и сводка — только подтверждённые; potential идут отдельным разделом «Требует уточнения».
    confirmed = [f for f in findings if f.status == "confirmed"]
    potential = [f for f in findings if f.status != "confirmed"]
    by_req: dict[str, list] = {r.rid: [] for r in result.requirements}
    for f in confirmed:
        if f.scope == "requirement":
            for rid in f.requirement_ids or []:
                by_req.setdefault(rid, []).append(f)
    req_orphans = [f for f in confirmed if f.scope == "requirement" and not f.requirement_ids]
    set_findings = [f for f in confirmed if f.scope == "set"]
    analysis_stages = [s for s in result.stages if s["name"] != "extract"]
    merged_map, dispute_reason = review_notes(result.stages)
    places, sources = block_places(result)
    merged_map = {k: [{**m, "reason": humanize_refs(m.get("reason", ""), places, sources)} for m in items]
                  for k, items in merged_map.items()}
    dispute_reason = {k: humanize_refs(v, places, sources) for k, v in dispute_reason.items()}

    ctx = dict(
        r=result,
        per_stage_models=len({m for s in result.stages for m in s.get("models", [])}) > 1,
        switches=[w for s in result.stages for w in s.get("model_switches", [])],
        top5=pick_main(findings),
        totals=run_totals(result.stages),
        by_req=by_req,
        req_orphans=req_orphans,
        set_findings=set_findings,
        analysis_ran=any(s["status"] == "ok" for s in analysis_stages),
        failed_stages=[s for s in result.stages if s["status"] != "ok"],
        dropped_total=sum(len(s.get("dropped", [])) for s in result.stages),
        n_confirmed=len(confirmed),
        potential=potential,
        merged_map=merged_map,
        dispute_reason=dispute_reason,
        sev_counts=Counter(f.severity for f in confirmed),
        kind_counts=Counter(f.kind for f in confirmed),
        reqs_with_findings=sum(1 for v in by_req.values() if v),
        clean_rids=[r.rid for r in result.requirements if not by_req.get(r.rid)],
        critical_list=[f for f in confirmed if f.kind in CRITICAL_KINDS],
        verdict_text=VERDICT_RU[result.verdict],
        summary_line=summary_line(result, confirmed),
        main_quotes_max=MAIN_QUOTES_MAX,
        SEVERITY_RU=SEVERITY_RU,
        KIND_RU=KIND_RU,
    )
    return _TEMPLATE.render(**ctx)


def write_report(result: RunResult, report_path: str | Path, started: float | None = None) -> tuple[Path, Path]:
    """Сначала report.json (данные прогона не теряются, даже если сборка Markdown упадёт), затем report.md.
    started — time.monotonic() на старте прогона: время прогона в отчёте считается до момента записи."""
    if started is not None:
        result.wall_seconds = round(time.monotonic() - started, 1)
    md = Path(report_path)
    md.parent.mkdir(parents=True, exist_ok=True)
    js = md.with_suffix(".json")
    js.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    md.write_text(render(result), encoding="utf-8")
    return md, js

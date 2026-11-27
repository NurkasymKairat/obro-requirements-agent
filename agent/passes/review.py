"""Проход «пропуски и оспаривание» одним вызовом.

Модель возвращает решения по уже найденным замечаниям (слить, изменить степень, добавить вторую цитату
противоречия, оспорить) и новые замечания. Решения применяет код: apply_review().

Контракт ядра (PassResult) сейчас передаёт только новые замечания и оспоренные fid, поэтому run()
возвращает их, а полный ответ модели кладёт в ReviewResult.decisions. Чтобы слияние и степень
дошли до отчёта, ядро должно вызвать apply_review(findings, result.decisions, verifier.find).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from agent.schemas import Finding, PassResult, Quote, RawFinding, RawQuote, Requirement

from .base import Client, load_prompt, render_requirements
from .calibrate import SEVERITIES, apply_floors, is_protected

NAME = "review"
# Замер на обоих документах: medium даёт ту же полноту на документе организаторов, что high, втрое быстрее;
# на high ответ обрезался по общему лимиту 32768, потому что рассуждение входит в лимит выхода.
THINKING = "medium"
MAX_OUTPUT_TOKENS = 65536


class Merge(BaseModel):
    into: str = Field(description="Идентификатор основного замечания, например F-003")
    duplicates: list[str] = Field(description="Идентификаторы замечаний с той же первопричиной")
    reason: str = Field(description="Какая первопричина общая")


REASONS = {"defined_elsewhere": "задано в другом месте", "duplicate": "дубль",
           "formatting": "оформление без влияния на реализацию"}


class SeverityChange(BaseModel):
    fid: str
    severity: Literal["critical", "high", "medium", "low"]
    reason_code: Literal["defined_elsewhere", "duplicate", "formatting"] = Field(
        description="Причина из закрытого списка: defined_elsewhere — задано в другом месте документа; "
                    "duplicate — дубль другого замечания; formatting — оформление без влияния на реализацию")
    reason: str = Field(description="Пояснение со ссылкой на место документа")


class AddedQuote(BaseModel):
    fid: str = Field(description="Идентификатор противоречия, которому не хватает второй стороны")
    quote: RawQuote


class Disputed(BaseModel):
    fid: str = Field(description="Идентификатор спорного замечания из списка, например F-007")
    reason: str = Field(description="Чем замечание опровергается, со ссылкой на место документа")


class ReviewOutput(BaseModel):
    merges: list[Merge] = Field(default_factory=list, description="Группы замечаний с одной первопричиной")
    severities: list[SeverityChange] = Field(default_factory=list, description="Только изменения степени")
    added_quotes: list[AddedQuote] = Field(default_factory=list, description="Вторые стороны противоречий")
    disputed: list[Disputed] = Field(default_factory=list, description="Спорные замечания")
    findings: list[RawFinding] = Field(default_factory=list, description="Новые замечания о пропущенных дефектах")


class ReviewResult(PassResult):
    decisions: ReviewOutput = Field(default_factory=ReviewOutput)


def render_findings(findings: Sequence[Finding]) -> str:
    lines = []
    for f in findings:
        quotes = "; ".join(f"«{q.text}» ({q.block_id or q.location})" for q in f.quotes)
        reqs = f", требования {', '.join(f.requirement_ids)}" if f.requirement_ids else ""
        lines.append(f"- {f.fid} [{f.kind}, {f.severity}, {f.origin_pass}{reqs}] {f.description}\n  Цитаты: {quotes}")
    return "\n".join(lines) if lines else "(замечаний нет)"


def run(client: Client, document_text: str, requirements: Sequence[Requirement],
        findings: Sequence[Finding] = ()) -> ReviewResult:
    system, user = load_prompt(NAME, document=document_text, requirements=render_requirements(requirements),
                               findings=render_findings(findings))
    out = client.complete(system, user, ReviewOutput, NAME)
    known = {f.fid for f in findings if not is_protected(f)}   # защищённые замечания не оспариваются
    return ReviewResult(findings=list(out.findings), decisions=out,
                        potential_fids=[d.fid for d in out.disputed if d.fid in known])


def _add_quotes(target: Finding, quotes: Sequence[Quote]) -> int:
    have = {(q.start, q.end) for q in target.quotes}
    added = 0
    for q in quotes:
        if (q.start, q.end) not in have:
            target.quotes.append(q)
            have.add((q.start, q.end))
            added += 1
    return added


def _same_place(a: Finding, b: Finding) -> bool:
    """Все цитаты b лежат в местах, которые уже процитированы в a."""
    return all(any(q.start < x.end and x.start < q.end for x in a.quotes) for q in b.quotes)


def _gist(text: str, limit: int = 300) -> str:
    """Суть замечания для дописывания: первые предложения в пределах limit символов."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:end + 1] if end > limit // 3 else cut.rstrip() + "…")


def apply_review(findings: Sequence[Finding], out: ReviewOutput,
                 find_quote: Callable[[RawQuote], Quote | None],
                 requirements: Sequence[Requirement] = ()) -> tuple[list[Finding], dict]:
    """Применить решения review к замечаниям. Возвращает (оставшиеся замечания, журнал решений).

    Порядок: вторые цитаты противоречий -> слияние -> степень -> оспаривание -> правила кода.
    При слиянии цитаты повтора переходят к основному замечанию; если повтор говорит о другом месте
    документа (его цитаты не пересекаются с цитатами основного), его суть дописывается к описанию.
    Правила кода, которые модель обойти не может:
      * степень меняется не больше чем на одну ступень и только с причиной из закрытого списка REASONS;
      * противоречие без цитат обеих сторон становится potential;
      * нижние границы степени по виду дефекта (calibrate.apply_floors) применяются последними;
      * замечания защищённых правил (calibrate.PROTECTED_ORIGINS) не оспариваются: правило само проверило
        весь документ, довод «очевидно из контекста» его не снимает.
    Новые замечания (out.findings) здесь не обрабатываются: их сверяет ядро, после чего к ним
    тоже нужно применить apply_floors.
    """
    items = [f.model_copy(deep=True) for f in findings]
    by_id = {f.fid: f for f in items}
    log: dict = {"before": len(items), "merged": [], "severity": [], "quotes_added": [], "quotes_rejected": [],
                 "disputed": [], "one_sided": [], "ignored": [],
                 "severity_rejected": [], "floors": [], "appended": [],
                 "dispute_rejected": []}

    for a in out.added_quotes:
        f = by_id.get(a.fid)
        q = find_quote(a.quote) if f else None
        if f is None:
            log["ignored"].append(f"added_quotes: нет {a.fid}")
        elif q is None:
            log["quotes_rejected"].append({"fid": a.fid, "quote": a.quote.text[:200]})
        elif _add_quotes(f, [q]):
            log["quotes_added"].append(a.fid)

    gone: set[str] = set()
    for m in out.merges:
        main = by_id.get(m.into)
        if main is None or m.into in gone:
            log["ignored"].append(f"merges: нет основного {m.into}")
            continue
        for fid in m.duplicates:
            dup = by_id.get(fid)
            if dup is None or fid == m.into or fid in gone:
                log["ignored"].append(f"merges: {fid} -> {m.into}")
                continue
            if not _same_place(main, dup):
                main.description = f"{main.description.rstrip()} Сюда же относится ({fid}): {_gist(dup.description)}"
                log["appended"].append({"into": m.into, "fid": fid})
            _add_quotes(main, dup.quotes)
            main.requirement_ids = list(dict.fromkeys(main.requirement_ids + dup.requirement_ids))
            origins = list(dict.fromkeys(main.origin_pass.split("+") + dup.origin_pass.split("+")))
            main.origin_pass = "+".join(origins)
            if dup.status == "confirmed":
                main.status = "confirmed"
            gone.add(fid)
            log["merged"].append({"into": m.into, "fid": fid, "reason": m.reason})

    for s in out.severities:
        f = by_id.get(s.fid)
        if f is None or s.fid in gone:
            log["ignored"].append(f"severities: {s.fid}")
        elif s.reason_code not in REASONS:
            log["severity_rejected"].append({"fid": s.fid, "why": f"причина вне списка: {s.reason_code}"})
        elif f.severity != s.severity:
            cur, want = SEVERITIES.index(f.severity), SEVERITIES.index(s.severity)
            new = SEVERITIES[cur + (1 if want > cur else -1)]      # не больше одной ступени
            log["severity"].append({"fid": s.fid, "from": f.severity, "to": new, "asked": s.severity,
                                    "reason_code": s.reason_code, "reason": s.reason, "lowered": want > cur})
            f.severity = new

    for d in out.disputed:
        f = by_id.get(d.fid)
        if f is None or d.fid in gone:
            log["ignored"].append(f"disputed: {d.fid}")
        elif is_protected(f):
            log["dispute_rejected"].append({"fid": d.fid, "reason": d.reason})
        elif f.status != "potential":
            f.status = "potential"
            log["disputed"].append({"fid": d.fid, "reason": d.reason})

    kept = [f for f in items if f.fid not in gone]
    for f in kept:
        if f.kind == "contradiction" and len(f.quotes) < 2 and f.status != "potential":
            f.status = "potential"
            log["one_sided"].append(f.fid)
    log["floors"] = apply_floors(kept, requirements)
    log["after"] = len(kept)
    return kept, log

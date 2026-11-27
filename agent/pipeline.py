"""Порядок проходов, сверка цитат, сборка RunResult."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from datetime import datetime
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from pathlib import Path

from . import __version__
from .llm import LLMClient
from .llm.base import AllModelsUnavailable, BalanceExhausted
from .parallel import pmap
from .passes import PASSES, extract
from .reader import read_document, render_for_llm
from .rules import run_rules
from .schemas import Finding, PassResult, Quote, RawFinding, RawRequirement, Requirement, RunResult
from .verify import Verifier

COUNTERS = ("calls", "retries", "cache_hits", "tokens_in", "tokens_out", "tokens_thinking", "thinking_zero")
# Уровень рассуждения прохода, если в модуле нет атрибута THINKING (off | low | medium | high;
# off и low — минимальный доступный уровень модели). Проход без атрибута и без строки здесь получает high.
DEFAULT_THINKING = {"extract": "off", "individual": "off", "cross_acceptance": "high", "cross_parts": "high",
                    "review": "high"}
THINKING_ALIASES = {"on": "high"}   # прежнее значение атрибута


def _own(stage: str, name: str) -> bool:
    return stage == name or stage.startswith(name + "[")


def pass_groups(passes: list) -> list[list]:
    """Группы для параллельного запуска: подряд идущие проходы без параметра findings — одна группа;
    проход, которому нужны находки предыдущих, — отдельная группа после них."""
    groups: list[list] = []
    for p in passes:
        independent = "findings" not in inspect.signature(p.run).parameters
        if independent and groups and groups[-1][-1:] and \
                "findings" not in inspect.signature(groups[-1][-1].run).parameters:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def review_last(passes: list) -> list:
    """Проход review — всегда последним: он работает с находками всех предыдущих."""
    return [p for p in passes if getattr(p, "NAME", "") != "review"] + \
           [p for p in passes if getattr(p, "NAME", "") == "review"]


def default_passes() -> list:
    """Состав из agent.passes.PASSES; review добавляется последним, если его нет в составе по умолчанию.
    Явно заданный состав (AGENT_PASSES, замеры сессии B) не дополняется."""
    passes = list(PASSES)
    if not os.getenv("AGENT_PASSES", "").strip() and all(getattr(p, "NAME", "") != "review" for p in passes):
        from .passes import review
        passes.append(review)
    return review_last(passes)


def thinking_mode(p) -> str:
    """Уровень рассуждения прохода: атрибут THINKING модуля, иначе DEFAULT_THINKING, иначе high."""
    mode = getattr(p, "THINKING", None) or DEFAULT_THINKING.get(getattr(p, "NAME", ""), "high")
    mode = THINKING_ALIASES.get(mode, mode)
    if mode not in ("off", "low", "medium", "high"):
        raise ValueError(f"{getattr(p, 'NAME', p)}: THINKING={mode!r}, допустимо off, low, medium, high")
    return mode


def max_output_tokens(p) -> int | None:
    """Лимит выходных токенов прохода: необязательный атрибут MAX_OUTPUT_TOKENS; None — лимит клиента."""
    v = getattr(p, "MAX_OUTPUT_TOKENS", None)
    if v is None:
        return None
    if not isinstance(v, int) or v <= 0:
        raise ValueError(f"{getattr(p, 'NAME', p)}: MAX_OUTPUT_TOKENS={v!r}, нужно положительное целое")
    return v


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
KIND_ORDER = {"contradiction": 0, "unverifiable": 1, "incomplete": 2, "ambiguous": 3,
              "design_in_requirement": 4, "other": 5}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def agent_version() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        rev = subprocess.run(["git", "describe", "--always", "--dirty", "--abbrev=7"], cwd=root,
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rev = ""
    # в контейнере нет git: коммит передаётся при сборке образа (ARG/ENV APP_COMMIT)
    rev = rev or os.getenv("APP_COMMIT", "").strip()
    return f"{__version__}+{rev}" if rev else __version__


def finding_sort_key(f: Finding):
    return (f.status != "confirmed", SEVERITY_ORDER[f.severity], KIND_ORDER[f.kind], f.fid)


class Pipeline:
    """extract -> сверка -> правила без модели -> проходы анализа (agent.passes.PASSES) -> RunResult."""

    def __init__(self, llm: LLMClient, passes: Sequence | None = None, rules: bool = True) -> None:
        self.llm = llm
        self.passes = default_passes() if passes is None else review_last(list(passes))
        self.use_rules = rules
        self.stages: list[dict] = []
        self.stop: BaseException | None = None

    def run(self, doc_path: str) -> RunResult:
        """Сбой модели не прерывает сборку отчёта: упавший проход помечается, отчёт становится неполным.
        Закончился баланс или недоступны все модели — остальные проходы пропускаются."""
        started = datetime.now().astimezone()
        self.t0 = time.monotonic()   # время прогона считается по часам от этой точки
        doc = read_document(doc_path)
        log(f"Документ: {doc_path} ({doc.format}, блоков: {len(doc.blocks)})")
        verifier = Verifier(doc)
        doc_text = render_for_llm(doc)
        self.stop: BaseException | None = None   # причина остановки оставшихся проходов
        requirements: list[Requirement] = []
        findings: list[Finding] = []

        # 1. Нарезка — без неё проверять нечего.
        raw_reqs, stage = self._stage(extract.NAME, lambda: extract.run(self.llm, doc_text), thinking_mode(extract),
                                      max_output_tokens(extract))
        if raw_reqs is None and self.stop is None:
            self.stop = RuntimeError(f"нарезка не выполнена: {stage.get('error')}")
        if raw_reqs is not None:
            requirements = self._verify_requirements(raw_reqs, verifier, stage)
            stage["items"] = len(requirements)
            log(f"  требований: {len(requirements)}, отброшено цитат: {len(stage['dropped'])}")
        known = {r.rid for r in requirements}

        # 2. Детерминированные правила: цитаты точные, сверка не нужна.
        if self.use_rules:
            res, stage = self._stage("rules", lambda: run_rules(doc, requirements))
            if res is not None:
                new = self._merge(findings, list(res))
                findings.extend(new)
                stage["items"] = len(new)
                log(f"  находок: {len(new)}")

        # 3. Проходы анализа. Подряд идущие независимые проходы (run без параметра findings) выполняются
        #    параллельно одной группой; проход, которому нужны находки предыдущих (review), — после неё.
        #    Результаты группы принимаются в порядке проходов, поэтому порядок находок и номера F-xxx
        #    не зависят от того, какой вызов вернулся первым. Сбой одного прохода не останавливает остальные.
        for group in pass_groups(self.passes):
            stages = [self._new_stage(p.NAME, thinking_mode(p), max_output_tokens(p)) for p in group]
            if self.stop is not None:              # остановка проверяется до группы, а не внутри неё:
                for st in stages:                  # состав отчёта не зависит от того, какой поток стартовал первым
                    self._skip(st)
                continue
            calls = [(st, (lambda p=p: p.run(self.llm, doc_text, requirements, **self._extra(p, findings))))
                     for p, st in zip(group, stages)]
            outs = pmap(lambda c: self._exec(*c, check_stop=False), calls)
            for p, st, out in zip(group, stages, outs):
                if out is not None:
                    self._absorb(p, st, out, findings, requirements, verifier, known)

        findings.sort(key=finding_sort_key)
        bad = [s for s in self.stages if s["status"] != "ok"]
        reason = None
        if bad:
            failed = ", ".join(f"{s['name']} ({'пропущен' if s['status'] == 'skipped' else 'сбой'})" for s in bad)
            reason = f"не выполнены проходы: {failed}"
            if self.stop is not None:
                reason += f". Причина остановки: {self.stop}"
        totals = run_totals(self.stages)
        return RunResult(
            document=str(doc_path),
            date=started.strftime("%Y-%m-%d %H:%M %Z").strip() or started.isoformat(timespec="minutes"),
            provider=getattr(self.llm, "name", "?"),
            model=models_used(self.stages) or getattr(self.llm, "model", "?"),
            agent_version=agent_version(),
            requirements=requirements,
            findings=findings,
            verdict="incomplete" if bad else compute_verdict(findings),
            first_fixes=compute_first_fixes(findings),
            stages=self.stages,
            incomplete_reason=reason,
            wall_seconds=round(time.monotonic() - self.t0, 1),   # уточняется при записи отчёта
            calls_seconds=totals["calls_seconds"],
        )

    # --- служебное ---

    @staticmethod
    def log_totals(result: RunResult) -> None:
        """Итоговая строка. Печатается после записи отчёта, чтобы время прогона было полным."""
        log(totals_line(result))
        if result.incomplete_reason:
            log(f"ПРОГОН НЕПОЛНЫЙ: {result.incomplete_reason}")

    def _llm_stats(self, name: str) -> dict:
        """Сумма счётчиков клиента по стадии name и её пачкам name[1], name[2]… (снимок под блокировкой)."""
        total: dict = {k: 0 for k in COUNTERS}
        total["models"] = {}
        total["call_log"] = []
        with getattr(self.llm, "_lock", nullcontext()):
            items = [(k, dict(v), list(v.get("call_log", []))) for k, v in getattr(self.llm, "stats", {}).items()]
            total["switches"] = [w for w in list(getattr(self.llm, "switches", [])) if _own(w.get("stage", ""), name)]
        for stage, st, calls in items:
            if _own(stage, name):
                for k in COUNTERS:
                    total[k] += st.get(k, 0)
                for m, n in st.get("models", {}).items():
                    total["models"][m] = total["models"].get(m, 0) + n
                total["call_log"] += [{"stage": stage, **c} for c in calls]
        return total

    def _stage(self, name: str, fn: Callable, thinking: str | None = None, max_out: int | None = None):
        stage = self._new_stage(name, thinking, max_out)
        return self._exec(stage, fn), stage

    def _new_stage(self, name: str, thinking: str | None = None, max_out: int | None = None) -> dict:
        """Стадия создаётся заранее, в порядке проходов: порядок stages не зависит от потоков."""
        stage: dict = {"name": name, "status": "ok", "thinking": thinking,
                       "max_output_tokens": max_out or getattr(self.llm, "max_output_tokens", None),
                       "seconds": 0.0, **{k: 0 for k in COUNTERS},
                       "models": [], "model_switches": [], "call_log": [], "items": 0, "dropped": []}
        self.stages.append(stage)
        return stage

    def _skip(self, stage: dict) -> None:
        stage["status"] = "skipped"
        stage["error"] = f"пропущен: {self.stop}"
        log(f"Проход {stage['name']} пропущен")

    def _exec(self, stage: dict, fn: Callable, check_stop: bool = True):
        name, thinking, max_out = stage["name"], stage["thinking"], stage.get("max_output_tokens")
        if max_out == getattr(self.llm, "max_output_tokens", None):
            max_out = None                         # лимит клиента — без переопределения
        if check_stop and self.stop is not None:
            self._skip(stage)
            return None
        log(f"Проход {name}…")
        before = self._llm_stats(name)
        t0 = time.monotonic()
        out = None
        scope = getattr(self.llm, "using_pass", None)
        try:
            if scope and (thinking or max_out):
                with scope(thinking=thinking, max_output_tokens=max_out):
                    out = fn()
            else:
                out = fn()
        except (BalanceExhausted, AllModelsUnavailable) as e:
            stage["status"] = "failed"
            stage["error"] = str(e)[:500]
            self.stop = e
            log(f"  [{name}] ОСТАНОВКА: {e}")
        except Exception as e:  # noqa: BLE001 — сбой прохода пишется в отчёт
            stage["status"] = "failed"
            stage["error"] = f"{type(e).__name__}: {e}"[:500]
            log(f"  [{name}] ОШИБКА: {stage['error']}")
        finally:
            stage["seconds"] = round(time.monotonic() - t0, 1)
            after = self._llm_stats(name)
            for k in COUNTERS:
                stage[k] = after[k] - before[k]
            stage["call_log"] = after["call_log"][len(before["call_log"]):]
            # модели, которые реально дали ответы на этом шаге (из API или из кеша)
            stage["models"] = [m for m, n in after["models"].items() if n - before["models"].get(m, 0) > 0]
            stage["model_switches"] = [w for w in list(getattr(self.llm, "switches", []))
                                       if _own(w.get("stage", ""), name)][len(before["switches"]):]
        return out

    @staticmethod
    def _extra(p, findings: list[Finding]) -> dict:
        return {"findings": list(findings)} if "findings" in inspect.signature(p.run).parameters else {}

    def _absorb(self, p, stage: dict, out, findings: list[Finding], requirements: list[Requirement],
                verifier: Verifier, known: set[str]) -> None:
        """Принять результат прохода: решения ревью, сверка цитат, слияние дублей, номера F-xxx."""
        res = out if isinstance(out, PassResult) else PassResult(findings=list(out))
        # Проход-ревью: решения по уже найденному (слить, изменить степень, вторая цитата, оспорить)
        # применяет функция модуля apply_review(findings, decisions, find_quote) -> (kept, log).
        decisions, apply = getattr(res, "decisions", None), getattr(p, "apply_review", None)
        is_review = decisions is not None and callable(apply)
        if is_review:
            kw = {"requirements": requirements} if "requirements" in inspect.signature(apply).parameters else {}
            kept, rlog = apply(findings, decisions, lambda rq: verifier.find(rq)[0], **kw)
            findings[:] = kept
            stage["review_log"] = rlog
            stage["merged"] = len(rlog.get("merged", []))
            stage["lowered"] = sum(1 for x in rlog.get("severity", []) if x.get("lowered"))
            stage["raised"] = sum(1 for x in rlog.get("severity", []) if not x.get("lowered"))
            stage["disputed"] = len(rlog.get("disputed", [])) + len(rlog.get("one_sided", []))
            stage["quotes_added"] = len(rlog.get("quotes_added", []))
        new = self._verify_findings(res.findings, p.NAME, verifier, known, stage)
        new = self._merge(findings, new)
        if is_review:
            # правила кода действуют и на новые замечания ревью: противоречие без второй стороны —
            # potential; нижние границы степени (agent.passes.calibrate)
            for f in new:
                if f.kind == "contradiction" and len(f.quotes) < 2 and f.status != "potential":
                    f.status = "potential"
                    rlog.setdefault("one_sided", []).append(f.fid)
                    stage["disputed"] += 1
            from .passes.calibrate import apply_floors
            floors = apply_floors(new, requirements)
            rlog.setdefault("floors", []).extend(floors)
            stage["floors"] = len(rlog["floors"])
        findings.extend(new)
        stage["items"] = len(new)
        stage["added"] = len(new)
        disputed = set(res.potential_fids)
        for f in findings:
            if f.fid in disputed and f.status != "potential":
                f.status = "potential"
                stage["potential"] = stage.get("potential", 0) + 1
        if "review_log" in stage:
            log(f"  ревью: слито {stage['merged']}, понижено {stage['lowered']}, повышено {stage['raised']}, "
                f"оспорено {stage['disputed']}, поднято до нижней границы {stage.get('floors', 0)}, "
                f"добавлено находок {stage['added']}, "
                f"вторых цитат {stage['quotes_added']}")
        log(f"  находок: {len(new)}, отброшено: {len(stage['dropped'])}, оспорено: {stage.get('potential', 0)}")


    @staticmethod
    def _verify_requirements(raw: list[RawRequirement], v: Verifier, stage: dict) -> list[Requirement]:
        found: list[tuple[Quote, RawRequirement]] = []
        seen: set[tuple[int, int]] = set()
        for r in raw:
            q, how = v.find(r.quote, whole_sentence=True)
            if q is None:
                stage["dropped"].append({"reason": how, "quote": r.quote.text[:200], "block_id": r.quote.block_id})
                continue
            if (q.start, q.end) in seen:
                stage["dropped"].append({"reason": "дубль цитаты", "quote": q.text[:200], "block_id": q.block_id})
                continue
            seen.add((q.start, q.end))
            if how == "fuzzy":
                stage.setdefault("fuzzy", 0)
                stage["fuzzy"] += 1
            found.append((q, r))
        found.sort(key=lambda x: x[0].start)
        out = []
        for i, (q, r) in enumerate(found, start=1):
            src = (r.source_id or "").strip().rstrip(".") or None
            loc = f"{q.location} ({src})" if src else q.location
            out.append(Requirement(rid=f"R-{i:03d}", source_id=src, section=r.section.strip() or q.location,
                                   summary=r.summary.strip(), quote=q.model_copy(update={"location": loc})))
        return out

    def _verify_findings(self, raw: list[RawFinding], origin: str, v: Verifier, known: set[str],
                         stage: dict) -> list[Finding]:
        out = []
        for rf in raw:
            quotes, lost = [], []
            for rq in rf.quotes:
                q, how = v.find(rq)
                if q is None:
                    lost.append(rq.text[:200])
                elif all((q.start, q.end) != (x.start, x.end) for x in quotes):
                    quotes.append(q)
            # противоречие без одной из сторон — не доказано
            if not quotes or (rf.kind == "contradiction" and lost):
                stage["dropped"].append({"reason": "цитата не найдена в документе", "quote": " / ".join(lost),
                                         "finding": rf.description[:200]})
                continue
            rids = [r for r in rf.requirement_ids if r in known]
            out.append(Finding(fid="", scope=rf.scope, requirement_ids=rids, criterion=rf.criterion,
                               standard=rf.standard, kind=rf.kind, severity=rf.severity, status="confirmed",
                               description=rf.description, quotes=quotes, fix=rf.fix, origin_pass=origin))
        return out

    @staticmethod
    def _merge(existing: list[Finding], new: list[Finding]) -> list[Finding]:
        """Простое слияние дублей: тот же вид дефекта и те же места цитат. Полная дедупликация — шаг 4."""
        def sig(f: Finding):
            return f.kind, frozenset((q.start, q.end) for q in f.quotes)

        index = {sig(f): f for f in existing}
        fresh = []
        # номер — следующий за наибольшим: после слияний ревью число замечаний меньше наибольшего номера
        n = max((int(f.fid[2:]) for f in existing if f.fid.startswith("F-") and f.fid[2:].isdigit()), default=0)
        for f in new:
            s = sig(f)
            if s in index:
                old = index[s]
                if SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[old.severity]:
                    old.severity = f.severity
                continue
            n += 1
            f.fid = f"F-{n:03d}"
            index[s] = f
            fresh.append(f)
        return fresh


def run_totals(stages: list[dict]) -> dict:
    t = {k: sum(s.get(k, 0) for s in stages) for k in COUNTERS}
    t["tokens_total"] = t["tokens_in"] + t["tokens_out"] + t["tokens_thinking"]
    t["seconds"] = sum(s.get("seconds", 0) for s in stages)
    # сумма длительностей отдельных вызовов (включая неудачные); при параллельных вызовах больше времени прогона
    t["calls_seconds"] = round(sum(e.get("seconds", 0) for s in stages for e in s.get("call_log", [])), 1)
    return t


def totals_line(result: RunResult) -> str:
    t = run_totals(result.stages)
    wall = f"{result.wall_seconds:.0f} с" if result.wall_seconds is not None else "—"
    return (f"Итого: вызовов модели {t['calls']} (повторов {t['retries']}, из кеша {t['cache_hits']}), "
            f"токены: вход {t['tokens_in']}, выход {t['tokens_out']}, рассуждение {t['tokens_thinking']}, "
            f"всего {t['tokens_total']}; время прогона {wall} (по часам, от старта до записи отчёта), "
            f"сумма длительностей вызовов {t['calls_seconds']:.0f} с")


def models_used(stages: list[dict]) -> str:
    """Модели, которые реально отвечали, в порядке появления."""
    seen: list[str] = []
    for st in stages:
        for m in st.get("models", []):
            if m not in seen:
                seen.append(m)
    return ", ".join(seen)


def compute_verdict(findings: list[Finding]) -> str:
    confirmed = [f for f in findings if f.status == "confirmed"]
    if any(f.severity == "critical" for f in confirmed):
        return "not_ready"
    if any(f.severity == "high" for f in confirmed):
        return "ready_with_fixes"
    return "ready"


def compute_first_fixes(findings: list[Finding], limit: int = 5) -> list[str]:
    top = [f for f in sorted(findings, key=finding_sort_key)
           if f.status == "confirmed" and f.severity in ("critical", "high")][:limit]
    return [f"{f.fid} — {f.quotes[0].location}: {f.fix}" for f in top]

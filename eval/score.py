"""Сверка report.json с эталоном дефектов eval/golden.yaml.

Запуск:
    python eval/score.py reports/report.json
    python eval/score.py reports/report.json --compare reports/report2.json
    python eval/score.py --check          # самопроверка эталона и утечек в промпты

Код возврата 1, если полнота ниже порога (--min-recall, по умолчанию 0.8),
есть выдуманные цитаты или самопроверка не прошла.

Скрипт не зависит от пакета agent: report.json читается как обычный JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = ROOT / "eval" / "golden.yaml"
PROMPTS_DIR = ROOT / "prompts"

_QUOTES = dict.fromkeys(map(ord, "«»“”„‟″"), '"') | dict.fromkeys(map(ord, "‘’‚′"), "'")
_DASHES = dict.fromkeys(map(ord, "–—−‑‒―"), "-")
_DROP = dict.fromkeys(map(ord, "*`|­​"), " ")


def norm(text: str) -> str:
    """Нормализация для сравнения: регистр, ё, кавычки, тире, разметка Markdown, пробелы."""
    text = text.replace(" ", " ").replace(" ", " ")
    text = text.translate(_QUOTES).translate(_DASHES).translate(_DROP)
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


def spans(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Все вхождения нормализованной строки needle в нормализованный haystack."""
    if not needle:
        return []
    out, pos = [], haystack.find(needle)
    while pos != -1:
        out.append((pos, pos + len(needle)))
        pos = haystack.find(needle, pos + 1)
    return out


def overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(s1 < e2 and s2 < e1 for s1, e1 in a for s2, e2 in b)


@dataclass
class Defect:
    id: str
    title: str
    kind: str
    severity: str
    core: bool
    where: str
    groups: list[list[list[tuple[int, int]]]]  # группа -> альтернатива -> вхождения
    min_groups: int
    keywords: list[re.Pattern]
    matched_by: list[str] = field(default_factory=list)   # fid
    passes: set[str] = field(default_factory=set)
    both_sides: bool = False
    confirmed: bool = False   # найден хотя бы одним замечанием со статусом confirmed


def load_golden(path: Path, doc_norm: str) -> tuple[list[Defect], list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    defects, errors, seen = [], [], set()
    for raw in data["defects"]:
        did = raw["id"]
        if did in seen:
            errors.append(f"{did}: повтор идентификатора")
        seen.add(did)
        groups = []
        for group in raw.get("anchors") or []:
            alts = []
            for anchor in group:
                found = spans(doc_norm, norm(anchor))
                if not found:
                    errors.append(f"{did}: якорь не найден в документе: {anchor!r}")
                alts.append(found)
            groups.append(alts)
        keywords = []
        for kw in raw.get("keywords") or []:
            try:
                keywords.append(re.compile(kw))
            except re.error as exc:
                errors.append(f"{did}: плохое выражение {kw!r}: {exc}")
        if not groups and not keywords:
            errors.append(f"{did}: нет ни якорей, ни ключевых слов")
        defects.append(Defect(
            id=did, title=raw["title"], kind=raw["kind"], severity=raw["severity"],
            core=bool(raw.get("core", False)), where=raw.get("where", ""),
            groups=groups, min_groups=int(raw.get("min_groups", 1 if groups else 0)),
            keywords=keywords,
        ))
    return defects, errors


def finding_passes(finding: dict) -> set[str]:
    raw = str(finding.get("origin_pass") or "?")
    return {p.strip() for p in re.split(r"[+,;/ ]+", raw) if p.strip()}


def score(report: dict, defects: list[Defect], doc_norm: str) -> dict:
    findings = report.get("findings", [])
    fabricated, no_quotes, unmatched = [], [], []
    multi = 0
    for f in findings:
        fid = f.get("fid", "?")
        quote_spans: list[tuple[int, int]] = []
        quotes = f.get("quotes") or []
        if not quotes:
            no_quotes.append(fid)
        for q in quotes:
            found = spans(doc_norm, norm(q.get("text", "")))
            if not found:
                fabricated.append((fid, q.get("text", "")))
            quote_spans.extend(found)
        text = norm(" ".join(str(f.get(k, "")) for k in ("description", "fix", "criterion")))
        hits = 0
        for d in defects:
            touched = sum(1 for g in d.groups if any(overlap(quote_spans, alt) for alt in g))
            if touched < d.min_groups:
                continue
            if not all(k.search(text) for k in d.keywords):
                continue
            d.matched_by.append(fid)
            d.passes |= finding_passes(f)
            d.confirmed = d.confirmed or f.get("status") != "potential"
            if d.groups and touched == len(d.groups):
                d.both_sides = True
            hits += 1
        if hits == 0:
            unmatched.append(f)
        if hits > 1:
            multi += 1

    found = [d for d in defects if d.matched_by]
    core = [d for d in defects if d.core]
    core_found = [d for d in core if d.matched_by]

    per_pass: dict[str, dict] = {}
    for f in findings:
        for p in finding_passes(f):
            per_pass.setdefault(p, {"findings": 0, "golden": 0, "unique": 0})["findings"] += 1
    for d in found:
        for p in d.passes:
            per_pass[p]["golden"] += 1
        if len(d.passes) == 1:
            per_pass[next(iter(d.passes))]["unique"] += 1

    return {
        "golden_total": len(defects),
        "golden_found": len(found),
        "recall": len(found) / len(defects) if defects else 0.0,
        "core_total": len(core),
        "core_found": len(core_found),
        "core_recall": len(core_found) / len(core) if core else 0.0,
        "found_only_potential": [d.id for d in found if not d.confirmed],
        "findings_total": len(findings),
        "findings_unmatched": len(unmatched),
        "findings_multi": multi,
        "fabricated_quotes": fabricated,
        "findings_without_quotes": no_quotes,
        "per_pass": per_pass,
        "defects": [
            {"id": d.id, "found": bool(d.matched_by), "core": d.core, "severity": d.severity,
             "kind": d.kind, "title": d.title, "matched_by": d.matched_by,
             "passes": sorted(d.passes), "both_sides": d.both_sides,
             "sides": len(d.groups), "confirmed": d.confirmed}
            for d in defects
        ],
        "unmatched": [
            {"fid": f.get("fid", "?"), "severity": f.get("severity"), "status": f.get("status"),
             "origin_pass": f.get("origin_pass"), "description": f.get("description", "")}
            for f in unmatched
        ],
    }


def critical_ids(report: dict, golden_path: Path, doc_norm: str) -> tuple[set[str], int]:
    """Эталонные дефекты, найденные подтверждёнными критичными замечаниями, и число критичных вне эталона."""
    crit = [f for f in report.get("findings", [])
            if f.get("severity") == "critical" and f.get("status") == "confirmed"]
    defects, _ = load_golden(golden_path, doc_norm)
    res = score({"findings": crit}, defects, doc_norm)
    return {d["id"] for d in res["defects"] if d["found"]}, res["findings_unmatched"]


def print_result(res: dict, report: dict, min_recall: float) -> None:
    print(f"Отчёт: модель {report.get('provider', '?')}/{report.get('model', '?')}, "
          f"версия {report.get('agent_version', '?')}, вердикт {report.get('verdict', '?')}")
    print()
    print(f"{'ID':<6} {'':<3} {'ядро':<5} {'важность':<9} {'проходы':<28} дефект")
    for d in res["defects"]:
        mark = "да" if d["found"] else "НЕТ"
        note = ""
        if d["found"] and d["kind"] == "contradiction" and d["sides"] > 1 and not d["both_sides"]:
            note = "  [процитирована одна сторона]"
        if d["found"] and not d["confirmed"]:
            note += "  [только potential]"
        print(f"{d['id']:<6} {mark:<3} {'*' if d['core'] else '':<5} {d['severity']:<9} "
              f"{','.join(d['passes']) or '-':<28} {d['title'][:90]}{note}")
    print()
    print("Вклад проходов (замечаний / найдено по эталону / найдено только этим проходом):")
    for name, s in sorted(res["per_pass"].items()):
        flag = "" if s["unique"] else "   <- ничего уникального по эталону"
        print(f"  {name:<20} {s['findings']:>3} / {s['golden']:>3} / {s['unique']:>3}{flag}")
    print()
    if res["unmatched"]:
        print(f"Замечания вне эталона ({len(res['unmatched'])}) — проверить вручную: ложное или новый дефект:")
        for f in res["unmatched"]:
            print(f"  {f['fid']} [{f['severity']}, {f['status']}, {f['origin_pass']}] {f['description'][:110]}")
        print()
    if res["findings_without_quotes"]:
        print("Замечания без цитат:", ", ".join(res["findings_without_quotes"]))
    if res["fabricated_quotes"]:
        print(f"ВЫДУМАННЫЕ ЦИТАТЫ ({len(res['fabricated_quotes'])}):")
        for fid, text in res["fabricated_quotes"]:
            print(f"  {fid}: {text[:120]!r}")
    print(f"Полнота: {res['golden_found']}/{res['golden_total']} = {res['recall']:.0%} "
          f"(порог {min_recall:.0%}); ядро: {res['core_found']}/{res['core_total']} = {res['core_recall']:.0%}")
    if res["found_only_potential"]:
        print("Найдены, но оспорены (только potential):", ", ".join(res["found_only_potential"]))
    print(f"Замечаний всего: {res['findings_total']}, вне эталона: {res['findings_unmatched']}, "
          f"покрывают несколько дефектов: {res['findings_multi']}, "
          f"выдуманных цитат: {len(res['fabricated_quotes'])}")


def check_prompts(doc_norm: str, golden_path: Path) -> list[str]:
    """Утечки в промпты: фрагменты эталона и дословные куски проверяемого документа."""
    errors = []
    if not PROMPTS_DIR.is_dir():
        return errors
    golden = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    needles = set()
    for d in golden["defects"]:
        needles.add(norm(d["title"]))
        for group in d.get("anchors") or []:
            needles.update(norm(a) for a in group)
    words = doc_norm.split()
    shingles = {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}
    for path in sorted(PROMPTS_DIR.rglob("*")):
        if not path.is_file():
            continue
        text = norm(path.read_text(encoding="utf-8"))
        for n in needles:
            if len(n.split()) >= 4 and n in text:   # короткие якоря — общие термины, не утечка
                errors.append(f"{path.name}: фрагмент эталона: {n!r}")
        pw = text.split()
        for i in range(len(pw) - 3):
            sh = " ".join(pw[i:i + 4])
            if sh in shingles:
                errors.append(f"{path.name}: дословный фрагмент документа: {sh!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Сверка report.json с эталоном дефектов")
    ap.add_argument("report", nargs="?", help="путь к report.json")
    ap.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    ap.add_argument("--doc", help="проверяемый документ (по умолчанию — из эталона)")
    ap.add_argument("--min-recall", type=float, default=0.8)
    ap.add_argument("--compare", help="второй report.json: сравнить списки критичных дефектов")
    ap.add_argument("--json", dest="json_out", help="записать результат сверки в JSON")
    ap.add_argument("--check", action="store_true", help="проверить эталон и промпты, без отчёта")
    args = ap.parse_args(argv)

    golden_path = Path(args.golden)
    meta = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    doc_path = Path(args.doc) if args.doc else ROOT / meta["document"]
    doc_norm = norm(doc_path.read_text(encoding="utf-8"))
    defects, errors = load_golden(golden_path, doc_norm)

    if args.check:
        errors += check_prompts(doc_norm, golden_path)
        for e in errors:
            print("ОШИБКА:", e)
        core = sum(d.core for d in defects)
        print(f"Эталон: {len(defects)} дефектов, из них ядро {core}. Ошибок: {len(errors)}")
        return 1 if errors else 0

    if errors:
        for e in errors:
            print("ОШИБКА ЭТАЛОНА:", e, file=sys.stderr)
        return 1
    if not args.report:
        ap.error("нужен путь к report.json или --check")

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    res = score(report, defects, doc_norm)
    print_result(res, report, args.min_recall)

    stable = True
    if args.compare:
        other = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        a, a_extra = critical_ids(report, golden_path, doc_norm)
        b, b_extra = critical_ids(other, golden_path, doc_norm)
        stable = a == b and a_extra == b_extra
        print()
        print(f"Критичные дефекты двух прогонов: {'совпадают' if stable else 'РАСХОДЯТСЯ'}")
        if a - b:
            print("  только в первом:", ", ".join(sorted(a - b)))
        if b - a:
            print("  только во втором:", ", ".join(sorted(b - a)))
        if a_extra != b_extra:
            print(f"  критичных вне эталона: {a_extra} против {b_extra}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = res["recall"] >= args.min_recall and not res["fabricated_quotes"] and stable
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

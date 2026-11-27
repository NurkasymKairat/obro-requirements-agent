"""Замер агента на эталонных документах: модели x документы -> таблица полноты.

    python eval/bench.py                                   # модель из .env, оба документа
    python eval/bench.py -m gemini-3.8-flash -m gemini-3.5-flash --fresh --label models

Каждый прогон — отдельный процесс `python -m agent` без запасных моделей (иначе замер смешает модели).
--fresh — отдельный пустой кеш на прогон: честное время и число вызовов.
Отчёты: reports/bench/<label>/<документ>__<модель>.{md,json}
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = {
    "main": ("docs/requirements.md", "eval/golden.yaml"),
    "other": ("eval/sample_other.md", "eval/golden_other.yaml"),
}


def run_one(label: str, doc_key: str, model: str | None, fresh: bool, extra_env: dict) -> dict:
    doc, golden = DOCS[doc_key]
    out_dir = ROOT / "reports" / "bench" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{doc_key}__{model or 'default'}"
    md, js = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    js.unlink(missing_ok=True)
    env = dict(os.environ, GEMINI_FALLBACK_MODELS="", OPENAI_FALLBACK_MODELS="", **extra_env)
    if model:
        env["GEMINI_MODEL"] = model
    if fresh:
        cache = ROOT / ".cache" / "bench" / label / stem
        shutil.rmtree(cache, ignore_errors=True)
        env["CACHE_DIR"] = str(cache)
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "agent", doc, str(md)], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    seconds = time.monotonic() - t0
    row = {"doc": doc_key, "model": model or "default", "seconds": seconds, "ok": js.is_file(), "json": str(js)}
    if not js.is_file():
        row["error"] = (proc.stderr.strip().splitlines() or ["нет вывода"])[-1][:200]
        return row
    score_json = out_dir / f"{stem}.score.json"
    subprocess.run([sys.executable, "eval/score.py", str(js), "--golden", golden, "--json", str(score_json)],
                   cwd=ROOT, capture_output=True, text=True)
    res = json.loads(score_json.read_text(encoding="utf-8"))
    report = json.loads(js.read_text(encoding="utf-8"))
    stages = report.get("stages", [])
    row.update(
        model=report.get("model", row["model"]),
        found=res["golden_found"], total=res["golden_total"],
        core_found=res["core_found"], core_total=res["core_total"],
        findings=res["findings_total"], unmatched=res["findings_unmatched"],
        fabricated=len(res["fabricated_quotes"]),
        calls=sum(s.get("calls", 0) for s in stages),
        retries=sum(s.get("retries", 0) for s in stages),
        dropped=sum(len(s.get("dropped", [])) for s in stages),
        failed=[s["name"] for s in stages if s.get("status") != "ok"],
        missed=[d["id"] for d in res["defects"] if not d["found"]],
        only_potential=res.get("found_only_potential", []),
        missed_core=[d["id"] for d in res["defects"] if not d["found"] and d["core"]],
        critical=sum(1 for f in report["findings"] if f["severity"] == "critical" and f["status"] == "confirmed"),
        potential=sum(1 for f in report["findings"] if f["status"] == "potential"),
        verdict=report.get("verdict"),
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Замер агента на эталонных документах")
    ap.add_argument("-m", "--model", action="append", help="модель Gemini; можно несколько раз")
    ap.add_argument("-d", "--doc", action="append", choices=sorted(DOCS), help="документ; по умолчанию оба")
    ap.add_argument("--label", default="run", help="имя папки замера в reports/bench")
    ap.add_argument("--fresh", action="store_true", help="пустой кеш: честное время и число вызовов")
    ap.add_argument("--passes", help="состав проходов через запятую (AGENT_PASSES)")
    ap.add_argument("--max-batch", help="наибольшая пачка прохода individual (INDIVIDUAL_MAX_BATCH)")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()

    extra = {}
    if args.passes:
        extra["AGENT_PASSES"] = args.passes
    if args.max_batch:
        extra["INDIVIDUAL_MAX_BATCH"] = args.max_batch
    combos = [(d, m) for m in (args.model or [None]) for d in (args.doc or sorted(DOCS))]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(lambda c: run_one(args.label, c[0], c[1], args.fresh, extra), combos))

    print(f"{'модель':<24} {'док':<6} {'полнота':<12} {'ядро':<10} {'замеч.':>6} {'вне эт.':>7} {'выдум.':>6} "
          f"{'отброш.':>7} {'вызовы':>6} {'повт.':>5} {'сек':>5}  пропущено")
    for r in rows:
        if not r["ok"]:
            print(f"{r['model']:<24} {r['doc']:<6} СБОЙ: {r.get('error')}")
            continue
        rec = f"{r['found']}/{r['total']} {r['found'] / r['total']:.0%}"
        core = f"{r['core_found']}/{r['core_total']}"
        note = ("  СБОЙ ПРОХОДОВ: " + ",".join(r["failed"])) if r["failed"] else ""
        if r.get("only_potential"):
            note += "  оспорены верные: " + " ".join(r["only_potential"])
        print(f"{r['model']:<24} {r['doc']:<6} {rec:<12} {core:<10} {r['findings']:>6} {r['unmatched']:>7} "
              f"{r['fabricated']:>6} {r['dropped']:>7} {r['calls']:>6} {r['retries']:>5} {r['seconds']:>5.0f}  "
              f"{' '.join(r['missed']) or '-'}{note}")
    (ROOT / "reports" / "bench" / args.label / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Замер прохода review поверх сохранённого report.json: один вызов модели на документ.

    python eval/apply_review.py reports/report.json docs/requirements.md reports/review/main.json

Остальные проходы не перезапускаются: берутся требования и замечания из сохранённого отчёта.
Печатает, сколько замечаний слито, понижено, оспорено, добавлено, и что попало бы в блок «Главное».
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import load_settings  # noqa: E402
from agent.llm import make_client  # noqa: E402
from agent.passes import review  # noqa: E402
from agent.passes.calibrate import SEVERITIES, apply_floors, pick_main  # noqa: E402
from agent.pipeline import Pipeline, compute_first_fixes, compute_verdict, finding_sort_key  # noqa: E402
from agent.reader import read_document, render_for_llm  # noqa: E402
from agent.schemas import RunResult  # noqa: E402
from agent.verify import Verifier  # noqa: E402

def main() -> int:
    ap = argparse.ArgumentParser(description="Проход review поверх сохранённого отчёта")
    ap.add_argument("report")
    ap.add_argument("document")
    ap.add_argument("out")
    args = ap.parse_args()

    os.environ.setdefault("GEMINI_FALLBACK_MODELS", "")
    result = RunResult.model_validate_json(Path(args.report).read_text(encoding="utf-8"))
    doc = read_document(args.document)
    verifier = Verifier(doc)
    client = make_client(load_settings())
    # Параметры замера: уровень рассуждения и лимит выхода только для этого вызова.
    if os.getenv("REVIEW_THINKING_LEVEL"):
        from agent.llm import gemini
        gemini.THINKING_LEVELS["on"] = os.environ["REVIEW_THINKING_LEVEL"]
    if os.getenv("REVIEW_MAX_OUTPUT_TOKENS"):
        client.max_output_tokens = int(os.environ["REVIEW_MAX_OUTPUT_TOKENS"])
    t0 = time.monotonic()

    res = review.run(client, render_for_llm(doc), result.requirements, findings=result.findings)
    seconds = time.monotonic() - t0
    kept, log = review.apply_review(result.findings, res.decisions, lambda rq: verifier.find(rq)[0],
                                    result.requirements)

    stage = {"name": review.NAME, "status": "ok", "dropped": []}
    pipe = Pipeline(client, passes=[], rules=False)
    known = {r.rid for r in result.requirements}
    new = pipe._verify_findings(res.findings, review.NAME, verifier, known, stage)
    new = [f for f in new if not any(
        f.kind == k.kind and {(q.start, q.end) for q in f.quotes} == {(q.start, q.end) for q in k.quotes} for k in kept)]
    top = max((int(f.fid.split("-")[1]) for f in result.findings), default=0)
    for i, f in enumerate(new, start=top + 1):
        f.fid = f"F-{i:03d}"
    for f in new:   # правила кода действуют и на новые замечания
        if f.kind == "contradiction" and len(f.quotes) < 2:
            f.status = "potential"
    log["floors"] += apply_floors(new, result.requirements)

    final = sorted(kept + new, key=finding_sort_key)
    log.update(new=[f.fid for f in new], new_dropped=stage["dropped"], final=len(final),
               main_before=[f.fid for f in pick_main(result.findings)], main_after=[f.fid for f in pick_main(final)])
    out = result.model_copy(update={
        "findings": final, "verdict": compute_verdict(final), "first_fixes": compute_first_fixes(final),
        "stages": result.stages + [{**stage, "calls": 1, "items": len(new), "seconds": round(seconds, 1),
                                    "review": log, **{k: v for k, v in getattr(client, "stats", {}).get(review.NAME, {}).items()
                                                      if isinstance(v, (int, float))}}]})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(out.model_dump_json(indent=2), encoding="utf-8")

    def sev(fs):
        return ", ".join(f"{s}: {sum(1 for f in fs if f.severity == s and f.status == 'confirmed')}"
                         for s in SEVERITIES)
    lowered = [s for s in log["severity"] if s["lowered"]]
    raised = [s for s in log["severity"] if not s["lowered"]]
    print(f"Замечаний до: {log['before']}  ({sev(result.findings)}; potential: "
          f"{sum(1 for f in result.findings if f.status == 'potential')})")
    print(f"Замечаний после: {len(final)}  ({sev(final)}; potential: {sum(1 for f in final if f.status == 'potential')})")
    print(f"Слито: {len(log['merged'])}; понижено: {len(lowered)}; повышено: {len(raised)}; "
          f"оспорено: {len(log['disputed'])}; противоречий без второй стороны -> potential: {len(log['one_sided'])}; "
          f"вторых цитат добавлено: {len(log['quotes_added'])}, не найдено в документе: {len(log['quotes_rejected'])}; "
          f"новых: {len(new)}, новых отброшено: {len(stage['dropped'])}; пропущено решений: {len(log['ignored'])}")
    print(f"Нижние границы кода подняли: {len(log['floors'])}; изменений степени отклонено: {len(log['severity_rejected'])}")
    st = getattr(client, "stats", {}).get(review.NAME, {})
    print(f"Время: {seconds:.0f} с; вызовов: {st.get('calls', '?')}, повторов: {st.get('retries', '?')}; токены: вход "
          f"{st.get('tokens_in', '?')}, выход {st.get('tokens_out', '?')}, рассуждение {st.get('tokens_thinking', '?')}")
    print(f"Вердикт: {result.verdict} -> {out.verdict}")
    print("Главное до:   ", " ".join(log["main_before"]) or "-")
    print("Главное после:", " ".join(log["main_after"]) or "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())

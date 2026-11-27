"""Сверка с эталоном: механика сопоставления и защита от утечки эталона в промпты."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("score", ROOT / "eval" / "score.py")
score = importlib.util.module_from_spec(spec)
sys.modules["score"] = score
spec.loader.exec_module(score)

DOC = """# Спецификация
* **A-1.** Срок хранения заказа — 30 дней.
* **A-2.** Заказ удаляется через «10 дней» после оплаты.
* **A-3.** Отчёт формируется быстро.
"""

GOLDEN = """
document: doc.md
defects:
  - id: G-01
    title: Срок хранения противоречит сроку удаления
    kind: contradiction
    severity: critical
    core: true
    anchors:
      - ["Срок хранения заказа — 30 дней"]
      - ["Заказ удаляется через «10 дней» после оплаты"]
    keywords: ["срок|дней"]
  - id: G-02
    title: Непроверяемая скорость отчёта
    kind: unverifiable
    severity: high
    core: false
    anchors:
      - ["Отчёт формируется быстро"]
"""


def finding(fid, quotes, description, origin="individual", severity="critical", status="confirmed"):
    return {"fid": fid, "severity": severity, "status": status, "origin_pass": origin,
            "description": description, "fix": "", "criterion": "",
            "quotes": [{"text": q} for q in quotes]}


@pytest.fixture
def env(tmp_path):
    golden = tmp_path / "golden.yaml"
    golden.write_text(GOLDEN, encoding="utf-8")
    doc_norm = score.norm(DOC)
    return golden, doc_norm


def run(env, findings):
    golden, doc_norm = env
    defects, errors = score.load_golden(golden, doc_norm)
    assert errors == []
    return score.score({"findings": findings}, defects, doc_norm)


def test_match_tolerates_markup_quotes_and_case(env):
    res = run(env, [finding("F-1", ['заказ удаляется через "10 дней"', "Срок  хранения заказа - 30 дней"],
                            "Сроки расходятся: 30 и 10 дней")])
    g1 = res["defects"][0]
    assert g1["found"] and g1["both_sides"] and res["recall"] == 0.5 and res["core_recall"] == 1.0
    assert res["fabricated_quotes"] == [] and res["findings_unmatched"] == 0


def test_keywords_are_required(env):
    res = run(env, [finding("F-1", ["Срок хранения заказа — 30 дней"], "Нет идентификатора владельца")])
    assert not res["defects"][0]["found"] and res["findings_unmatched"] == 1


def test_fabricated_quote_is_reported(env):
    res = run(env, [finding("F-1", ["Заказ хранится вечно"], "Срок не задан")])
    assert res["fabricated_quotes"] == [("F-1", "Заказ хранится вечно")]
    assert res["golden_found"] == 0


def test_unique_contribution_per_pass(env):
    res = run(env, [
        finding("F-1", ["Срок хранения заказа — 30 дней"], "Срок 30 дней против 10", origin="cross_parts"),
        finding("F-2", ["Срок хранения заказа — 30 дней"], "Срок иной", origin="individual"),
        finding("F-3", ["Отчёт формируется быстро"], "Нет числа", origin="individual"),
    ])
    assert res["per_pass"]["individual"] == {"findings": 2, "golden": 2, "unique": 1}
    assert res["per_pass"]["cross_parts"] == {"findings": 1, "golden": 1, "unique": 0}


def test_missing_anchor_is_golden_error(env, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(GOLDEN.replace("Отчёт формируется быстро", "Отчёт формируется медленно"), encoding="utf-8")
    _, errors = score.load_golden(bad, env[1])
    assert len(errors) == 1 and "G-02" in errors[0]


def test_real_golden_is_valid_and_not_leaked_into_prompts():
    doc_path = ROOT / "docs" / "requirements.md"
    if not doc_path.is_file():
        pytest.skip("проверяемый документ не лежит в репозитории")
    doc_norm = score.norm(doc_path.read_text(encoding="utf-8"))
    defects, errors = score.load_golden(score.DEFAULT_GOLDEN, doc_norm)
    assert errors == [] and len(defects) >= 25
    assert score.check_prompts(doc_norm, score.DEFAULT_GOLDEN) == []

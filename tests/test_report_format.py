"""Оформление Markdown-отчёта: строка итога, приложение с ходом прогона, «Главное», цитаты."""
from agent.report import (calls_in, dedupe_quotes, headline, main_description, render, stage_ru,
                          summary_line)
from agent.schemas import Finding, Quote, Requirement, RunResult


def q(text, start, end, block="B-010", loc="Раздел, строка 10"):
    return Quote(text=text, block_id=block, start=start, end=end, location=loc)


def finding(fid="F-001", quotes=None, description="Первое предложение. Второе.", severity="critical",
            kind="contradiction", scope="requirement"):
    return Finding(fid=fid, scope=scope, requirement_ids=["R-001"], criterion="согласованность",
                   standard="ISO 29148 п. 5.2.6", kind=kind, severity=severity, status="confirmed",
                   description=description, quotes=quotes or [q("abc", 0, 3)], fix="Исправить.", origin_pass="x")


def result(findings, verdict="not_ready", stages=None):
    req = Requirement(rid="R-001", source_id="Ф-1", section="Раздел", summary="требование", quote=q("abc", 0, 3))
    return RunResult(document="doc.md", date="2026-11-27 10:00 +05", provider="gemini", model="m",
                     agent_version="0.1.0+abc", requirements=[req], findings=findings, verdict=verdict,
                     first_fixes=[], stages=stages or [{"name": "extract", "calls": 1},
                                                       {"name": "review", "calls": 1, "thinking": "medium",
                                                        "thinking_zero": 1}])


def test_stage_names_and_calls_agreement():
    assert stage_ru("extract") == "нарезка требований (extract)"
    assert stage_ru("parts_goals") == "цели и части документа (parts_goals)"
    assert stage_ru("review") == "итоговая сверка (review)"
    assert stage_ru("new_pass") == "new_pass"
    assert calls_in(1) == "в 1 вызове" and calls_in(3) == "в 3 вызовах" and calls_in(11) == "в 11 вызовах"
    assert calls_in(21) == "в 21 вызове"


def test_headline_first_sentence_without_merges():
    text = "Короткая суть. Дальше подробности. Сюда же относится (F-002): другое."
    assert headline(text) == "Короткая суть."
    long = "Слово " * 40 + "конец."
    h = headline(long)
    assert len(h) <= 121 and h.endswith("Слово…") and "..." not in h
    assert main_description(text) == "Короткая суть. Дальше подробности."


def test_dedupe_keeps_fuller_quote_from_same_block():
    a, inner, same, other = q("abcdef", 0, 6), q("bcd", 1, 4), q("abcdef", 0, 6), q("bcd", 1, 4, block="B-020")
    assert dedupe_quotes([inner, a, same, other]) == [a, other]


def test_summary_line_from_verdict_text():
    fs = [finding(), finding("F-002", severity="critical"), finding("F-003", severity="high")]
    assert summary_line(result(fs), fs) == "Итог: начинать разработку и тестирование нельзя — 2 критичных дефекта"
    one = [finding(severity="high")]
    assert summary_line(result(one, "ready_with_fixes"), one).endswith("— 1 дефект высокой важности")


def test_render_layout():
    quotes = [q(f"цитата {i}", 10 * i, 10 * i + 8, block=f"B-0{i}") for i in range(5)]
    merged = finding(quotes=quotes, description="Главная суть. Детали. Сюда же относится (F-007): слитое.")
    md = render(result([merged]))
    passport_end = md.index("## Главное")
    assert "**Итог: начинать разработку и тестирование нельзя — 1 критичный дефект**" in md[:passport_end]
    assert "| Проход |" not in md[:md.index("## 6. Итог")]
    appendix = md[md.index("## Приложение. Ход прогона"):]
    assert "нарезка требований (extract)" in appendix and "не рассуждала в 1 вызове" in appendix
    main = md[passport_end:md.index("## 2.")]
    assert "### 1. Противоречие: Главная суть." in main
    assert "Сюда же относится" not in main
    assert main.count("- Цитата") == 3 and "- Ещё 2 цитаты в разделе 3." in main
    assert "(F-007)" not in md and "Сюда же относится (объединено с похожим замечанием)" in md

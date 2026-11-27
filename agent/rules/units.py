"""Единицы измерения: величины одной размерности в разных единицах и целое против дробного.

Правило без модели. Замечание выдаётся, когда одновременно:
  * в одном требовании (блоке) или у одной и той же величины единицы одной размерности объявлены
    по-разному — либо для одной величины одно место требует целое значение, а другое дробное;
  * в документе нет правила пересчёта, округления или сравнения.

Словари ниже — данные. Чтобы правило знало новую единицу или величину, дополняется словарь, а не условия.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from agent.schemas import Finding

from .base import STD_SET, Doc, Line, finding

# Размерность -> единица -> (семейство, шаблон написаний). Единицы одного семейства пересчитываются
# постоянным множителем (тенге и тиын, секунда и минута); разные семейства — по внешнему курсу.
DIMENSIONS: dict[str, dict] = {
    "money": {"title": "деньги", "units": {
        "тенге": ("KZT", r"тенге|тг|₸|kzt"), "тиын": ("KZT", r"тиын\w*|тиин\w*"),
        "рубль": ("RUB", r"рубл\w*|руб|₽|rub"), "копейка": ("RUB", r"копе[йе]\w*|коп"),
        "доллар": ("USD", r"доллар\w*|\$|usd"), "цент": ("USD", r"цент\w*"),
        "евро": ("EUR", r"евро|€|eur"), "сом": ("KGS", r"сом\w*|kgs"), "сум": ("UZS", r"сум(?:ах|ы|ов)?|uzs"),
    }},
    "time": {"title": "время", "units": {
        "миллисекунда": ("time", r"миллисекунд\w*|мс|ms"), "секунда": ("time", r"секунд\w*|сек|sec\w*"),
        "минута": ("time", r"минут\w*|мин"), "час": ("time", r"час(?:ах|ы|ов|а)?"),
        "сутки": ("time", r"сутк\w*|суток|дн(?:ях|и|ей)|день|дня"),
    }},
    "data": {"title": "объём данных", "units": {
        "байт": ("data", r"байт\w*|bytes?"), "килобайт": ("data", r"килобайт\w*|кб|kb"),
        "мегабайт": ("data", r"мегабайт\w*|мб|mb"), "гигабайт": ("data", r"гигабайт\w*|гб|gb"),
    }},
    "share": {"title": "доли", "units": {
        "процент": ("share", r"процент\w*|%"), "промилле": ("share", r"промилле|‰"),
        "доля единицы": ("share", r"дол(?:ях|и|ей) единицы"), "базисный пункт": ("share", r"базисн\w+ пункт\w*|б\.\s?п\."),
    }},
}
# Величина -> основы слов, которыми её называют. По основе определяется «одна и та же величина».
QUANTITIES: dict[str, dict[str, str]] = {
    "money": {"сумма": r"сумм\w*|amount|sum", "порог": r"порог\w*|threshold", "лимит": r"лимит\w*|limit",
              "остаток": r"остат\w*|баланс\w*|balance", "цена": r"цен[аеуыой]\w*|стоимост\w*|price|cost",
              "комиссия": r"комисси\w*|fee", "расходы": r"расход\w*|затрат\w*", "платёж": r"плат[её]ж\w*|payment"},
    "time": {"срок": r"срок\w*", "длительность": r"длительност\w*|продолжительност\w*|duration",
             "таймаут": r"таймаут\w*|тайм-аут\w*|timeout", "интервал": r"интервал\w*|период\w*|interval",
             "задержка": r"задержк\w*|delay", "время": r"врем(?:я|ени|енем)"},
    "data": {"размер": r"размер\w*|size", "объём": r"объ[её]м\w*|volume"},
    "share": {"ставка": r"ставк\w*|rate", "доля": r"дол[яеию]\b", "скидка": r"скидк\w*|discount"},
}
# Слова, по которым в документе распознаётся правило пересчёта, округления или сравнения.
RULE_SAME_FAMILY = r"округл|пересч[её]т|пересчит|привод\w+ к|сравнива\w+ в|в единых единицах|=\s*100\b|[×x*]\s*100\b|умнож|дел[ия]т\w* на"
RULE_OTHER_FAMILY = r"\bкурс\w*|конверт\w*|пересч[её]т|пересчит"
RULE_ROUNDING = r"округл|усека|отбрасыва\w+|дробн\w+ част\w+ не"
# Признаки целого и дробного формата значения.
INTEGER_MARK = r"\bцел(?:ое|ого|ым|ом|ые|ых|ый|ую|ой)\b"
FRACTION_MARK = (r"(?:\d+|один|одного|два|двух|три|трех|четыре|четырех)\s+знак\w*\s+после\s+запят\w+|\bдробн\w+|"
                 r"с\s+точностью\s+до\s+(?:0[.,]\d+|сотых|десятых|тысячных)|\bdecimal\b")

_B, _E = r"(?<![\w$₸₽€%‰])", r"(?![\w])"
_CLAUSE_BREAK = re.compile(r"[,;:()|«»]")


def _alternation(dim: str) -> str:
    return "|".join(f"(?P<u{i}>{pat})" for i, (_, pat) in enumerate(DIMENSIONS[dim]["units"].values()))


_DECLARED = {dim: re.compile(rf"\bв\s+(?:цел\w+\s+)?(?:{_alternation(dim)}){_E}", re.I) for dim in DIMENSIONS}
_LABEL = {dim: re.compile(rf"[,(]\s*(?:{_alternation(dim)})\s*\)?{_E}", re.I) for dim in DIMENSIONS}
_ANY_UNIT = {dim: re.compile(rf"{_B}(?:{_alternation(dim)}){_E}", re.I) for dim in DIMENSIONS}
_NOUN = {dim: re.compile("|".join(rf"(?P<n{i}>{_B}(?:{pat}){_E})" for i, pat in enumerate(QUANTITIES[dim].values())), re.I)
         for dim in DIMENSIONS}


@dataclass(frozen=True)
class Mention:
    dim: str
    unit: str
    family: str
    noun: str | None
    line: Line
    start: int      # позиции в документе
    end: int


def _unit_of(dim: str, m: re.Match) -> tuple[str, str]:
    names = list(DIMENSIONS[dim]["units"])
    idx = next(i for i in range(len(names)) if m.group(f"u{i}"))
    return names[idx], DIMENSIONS[dim]["units"][names[idx]][0]


def _nouns(dim: str, text: str) -> list[tuple[str, int]]:
    names = list(QUANTITIES[dim])
    return [(names[next(i for i in range(len(names)) if m.group(f"n{i}"))], m.start()) for m in _NOUN[dim].finditer(text)]


def _mentions(doc: Doc) -> list[Mention]:
    """Объявления единиц: «<величина> в <единицах>» и подписи вида «Величина, ед.» или «Величина (ед.)»."""
    out = []
    for line in doc.lines:
        if line.in_code or line.is_separator or not line.text.strip():
            continue
        text = line.text.replace("ё", "е")
        for dim in DIMENSIONS:
            for pattern, need_noun in ((_DECLARED[dim], False), (_LABEL[dim], True)):
                for m in pattern.finditer(text):
                    clause_start = max([b.end() for b in _CLAUSE_BREAK.finditer(text, 0, m.start())] or [0])
                    nouns = _nouns(dim, text[clause_start:m.start()])
                    if need_noun and not nouns:
                        continue
                    noun, pos = nouns[-1] if nouns else (None, None)
                    begin = clause_start + pos if pos is not None else m.start()
                    unit, family = _unit_of(dim, m)
                    out.append(Mention(dim, unit, family, noun, line, line.start + begin, line.start + m.end()))
    return out


def _clause_span(line: Line, pos: int) -> tuple[int, int]:
    """Часть строки вокруг позиции pos до ближайших знаков препинания; для строки таблицы — вся строка."""
    if line.is_table_row:
        return line.content_span()
    text = line.text
    left = max([m.end() for m in re.finditer(r"[,;:(]|\.\s", text[:pos])] or [0])
    right_m = re.search(r"[,;)]|\.(?:\s|$)", text[pos:])
    right = pos + right_m.start() if right_m else len(text.rstrip())
    while left < right and text[left] in " *":
        left += 1
    return line.start + left, line.start + right


def check(doc: Doc) -> list[Finding]:
    return _mixed_units(doc) + _integer_vs_fraction(doc, doc.text.lower().replace("ё", "е"))


def _has_conversion_rule(doc: Doc, dim: str, rule: str) -> bool:
    """Правило пересчёта считается найденным, если слово о пересчёте стоит в одной строке с единицей
    этой размерности или со словом «единица»: «пересчёт итогов» без единиц правилом пересчёта не является."""
    for line in doc.lines:
        text = line.text.lower().replace("ё", "е")
        if re.search(rule, text) and (_ANY_UNIT[dim].search(text) or "единиц" in text):
            return True
    return False


def _mixed_units(doc: Doc) -> list[Finding]:
    out = []
    mentions = _mentions(doc)
    for dim, spec in DIMENSIONS.items():
        ms = [m for m in mentions if m.dim == dim]
        pairs = [(a, b) for i, a in enumerate(ms) for b in ms[i + 1:]
                 if a.unit != b.unit and (a.line.no == b.line.no or (a.noun and a.noun == b.noun))]
        if not pairs:
            continue
        # сначала пары из одного требования: там величины стоят рядом и сравниваются
        a, b = sorted(pairs, key=lambda p: (p[0].line.no != p[1].line.no, p[0].line.no))[0]
        rule = RULE_SAME_FAMILY if a.family == b.family else RULE_OTHER_FAMILY
        if _has_conversion_rule(doc, dim, rule):
            continue
        qa, qb = doc.quote(a.start, a.end), doc.quote(b.start, b.end)
        what = "пересчёта между ними, округления и сравнения" if a.family == b.family else "пересчёта (курс, момент и округление)"
        out.append(finding(
            "units", criterion="однозначность (unambiguous)", kind="ambiguous", severity="high", standard=STD_SET,
            description=f"Величины одной размерности ({spec['title']}) заданы в разных единицах: «{qa.text}» — {a.unit}, "
                        f"«{qb.text}» — {b.unit}. Правила {what} в документе нет: неясно, в каких единицах "
                        "значения сравниваются и складываются и как округляется результат.",
            quotes=[qa, qb],
            fix=f"Привести величины к одной единице либо добавить правило: в каких единицах выполняется сравнение, "
                f"как {a.unit} пересчитывается в {b.unit} и как округляется результат.",
        ))
    return out


def _integer_vs_fraction(doc: Doc, low: str) -> list[Finding]:
    if re.search(RULE_ROUNDING, low):
        return []
    out = []
    for dim, spec in DIMENSIONS.items():
        ints, fracs = [], []
        for line in doc.lines:
            if line.in_code or line.is_separator:
                continue
            text = line.text.replace("ё", "е")
            for marks, bucket in ((INTEGER_MARK, ints), (FRACTION_MARK, fracs)):
                m = re.search(marks, text, re.I)
                if not m:
                    continue
                start, end = _clause_span(line, m.start())
                nouns = {n for n, _ in _nouns(dim, doc.text[start:end].replace("ё", "е"))}
                if nouns:
                    bucket.append((line, start, end, nouns))
        pair = next(((i, f) for i in ints for f in fracs if i[0].no != f[0].no and i[3] & f[3]), None)
        if not pair:
            continue
        (_, s1, e1, n1), (_, s2, e2, n2) = pair
        name = sorted(n1 & n2)[0]
        qi, qf = doc.quote(s1, e1), doc.quote(s2, e2)
        out.append(finding(
            "units", criterion="однозначность (unambiguous)", kind="ambiguous", severity="high", standard=STD_SET,
            description=f"Одна и та же величина ({name}, {spec['title']}) в одном месте задана целым значением, "
                        f"в другом — дробным: «{qi.text}» и «{qf.text}». Правила округления в документе нет: "
                        "неясно, что происходит с дробной частью при сохранении, сравнении и отображении.",
            quotes=[qi, qf],
            fix="Задать единую точность величины во всех местах либо добавить правило округления: "
                "до скольких знаков, в какую сторону и на каком шаге.",
        ))
    return out

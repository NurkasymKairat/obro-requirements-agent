"""Сверка цитат из ответа модели с текстом документа.

Цитата ищется после нормализации (регистр, пробелы, кавычки, тире, разметка Markdown):
сначала в указанном блоке, затем во всём документе, затем нечётко (≥ FUZZY_MIN совпавших символов)
в блоке и соседних. Найденная цитата заменяется дословным фрагментом документа с позициями.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import Block, Document, Quote, RawQuote

FUZZY_MIN = 0.9
FUZZY_MIN_CHARS = 25      # короче — только точное совпадение
MIN_QUOTE_CHARS = 4

_QUOTES = dict.fromkeys("«»„“”‟″\"", '"') | dict.fromkeys("‘’‚‛′`'", "'")
_DASHES = dict.fromkeys("‐‑‒–—―−", "-")
_SPACES = set("           \t\n\r\f\v |")
_MARKUP = set("*`_")
_DROP = set("*_​‌‍﻿`#>")


def normalize(s: str) -> tuple[str, list[int]]:
    """Возвращает нормализованную строку и для каждого её символа — индекс в исходной."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        if ch in _DROP:
            continue
        if ch in _SPACES or ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                idx.append(i)
            continue
        ch = _QUOTES.get(ch) or _DASHES.get(ch) or ch
        ch = ch.lower()
        if ch == "ё":
            ch = "е"
        for c in ch:
            out.append(c)
            idx.append(i)
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def norm(s: str) -> str:
    return normalize(s)[0].strip()


class Verifier:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.by_id = {b.block_id: b for b in doc.blocks}
        self.order = {b.block_id: i for i, b in enumerate(doc.blocks)}
        self.doc_norm, self.doc_idx = normalize(doc.text)

    def block_at(self, pos: int) -> Block | None:
        for b in self.doc.blocks:
            if b.start <= pos < b.end:
                return b
        best = None
        for b in self.doc.blocks:
            if b.start <= pos:
                best = b
        return best

    def find(self, rq: RawQuote, whole_sentence: bool = False) -> tuple[Quote | None, str]:
        """Возвращает (Quote, способ) или (None, причина).

        Границы найденной цитаты достраиваются в пределах блока: парные кавычки и скобки не теряются,
        знак конца предложения сразу после цитаты включается. whole_sentence=True (нарезка требований)
        дополнительно дотягивает конец цитаты до конца предложения, ячейки таблицы или блока.
        """
        q = norm(rq.text)
        if len(q) < MIN_QUOTE_CHARS:
            return None, "пустая или слишком короткая цитата"
        hint = self.by_id.get(rq.block_id.strip().strip("[]"))

        for cand in _variants(q):
            hits = _all_occurrences(self.doc_norm, cand)
            if hits:
                if hint:
                    inside = [h for h in hits if hint.start <= self.doc_idx[h] < hint.end]
                    if inside:
                        hits = inside
                return self._make(hits[0], hits[0] + len(cand), whole_sentence), "exact"

        if len(q) < FUZZY_MIN_CHARS:
            return None, "цитата не найдена в документе"
        cands = []
        if hint:
            i = self.order[hint.block_id]
            cands = [hint] + self.doc.blocks[max(0, i - 2):i] + self.doc.blocks[i + 1:i + 3]
        for blk in cands or self.doc.blocks:
            span = self._fuzzy_in(blk, q)
            if span:
                return self._make(*span, whole_sentence), "fuzzy"
        if hint:
            for blk in self.doc.blocks:
                span = self._fuzzy_in(blk, q)
                if span:
                    return self._make(*span, whole_sentence), "fuzzy"
        return None, "цитата не найдена в документе"

    def _fuzzy_in(self, blk: Block, q: str) -> tuple[int, int] | None:
        # нормализованное окно блока внутри нормализованного документа
        lo = _first_ge(self.doc_idx, blk.start)
        hi = _first_ge(self.doc_idx, blk.end)
        hay = self.doc_norm[lo:hi]
        if len(hay) < len(q) * FUZZY_MIN:
            return None
        sm = SequenceMatcher(None, hay, q, autojunk=False)
        blocks = [m for m in sm.get_matching_blocks() if m.size >= 3]
        matched = sum(m.size for m in blocks)
        if not blocks or matched < FUZZY_MIN * len(q):
            return None
        s = blocks[0].a
        e = blocks[-1].a + blocks[-1].size
        if (e - s) > len(q) * 1.2:
            return None
        # числа, отрицания и единицы не должны расходиться: иначе цитата меняет смысл
        if _digits(hay[s:e]) != _digits(q) or _negs(hay[s:e]) != _negs(q):
            return None
        return lo + s, lo + e

    def _make(self, ns: int, ne: int, whole_sentence: bool = False) -> Quote:
        start = self.doc_idx[ns]
        end = self.doc_idx[ne - 1] + 1
        blk = self.block_at(start)
        if blk and start < blk.end:
            start, end = _complete(self.doc.text, start, end, blk.start, max(blk.end, end),
                                   whole_sentence and blk.kind != "code")
        # не резать разметку пополам: «REST type** | GET» -> «**REST type** | GET»
        t = self.doc.text
        while start > 0 and t[start - 1] in _MARKUP:
            start -= 1
        while end < len(t) and t[end] in _MARKUP:
            end += 1
        text = self.doc.text[start:end]
        blk = self.block_at(start)
        return Quote(text=text, block_id=blk.block_id if blk else "", start=start, end=end,
                     location=blk.location if blk else f"символы {start}–{end}")


_PAIRS = {"«": "»", "(": ")", "[": "]", "{": "}", "“": "”"}
_CLOSERS = {v: k for k, v in _PAIRS.items()}
_TERMINATORS = ".!?…;"
_SENTENCE_END = re.compile(r"[.!?…](?=[\s*_`)»”\]]|$)|\s\|")


def _variants(q: str) -> list[str]:
    """Варианты нормализованной цитаты: как есть; без обёртки кавычками; без многоточия и точки на краях.
    Обёртка снимается только парой с обеих сторон, чтобы не потерять настоящую кавычку документа."""
    out = [q]
    if len(q) > 8 and q[0] == q[-1] and q[0] in "\"'":
        out.append(q[1:-1].strip())
    for v in list(out):
        t = v.strip(" …").rstrip(".").strip()
        if len(t) >= MIN_QUOTE_CHARS and t not in out:
            out.append(t)
    return out


def _complete(text: str, start: int, end: int, lo: int, hi: int, whole_sentence: bool) -> tuple[int, int]:
    """Достраивает границы [start, end) в пределах [lo, hi): парные знаки, конец предложения."""
    for _ in range(3):  # расширение может открыть новую пару — несколько проходов
        s0, e0 = start, end
        start, end = _balance(text, start, end, lo, hi)
        if whole_sentence:
            end = _sentence_end(text, end, hi)
        # знак конца предложения сразу за цитатой: «… REST API»|.  -> «… REST API».
        while end < hi and text[end] in _TERMINATORS and (end + 1 >= hi or not text[end + 1].isalnum()):
            end += 1
        if (start, end) == (s0, e0):
            break
    return start, end


def _balance(text: str, start: int, end: int, lo: int, hi: int) -> tuple[int, int]:
    stack: list[str] = []
    missing_open: list[str] = []
    for ch in text[start:end]:
        if ch in _PAIRS:
            stack.append(ch)
        elif ch in _CLOSERS:
            if stack and stack[-1] == _CLOSERS[ch]:
                stack.pop()
            else:
                missing_open.append(_CLOSERS[ch])
    # незакрытые открывающие — ищем закрывающие вперёд
    e = end
    while stack and e < hi:
        ch = text[e]
        if ch in _PAIRS:
            stack.append(ch)
        elif ch in _CLOSERS and stack and stack[-1] == _CLOSERS[ch]:
            stack.pop()
        e += 1
    if not stack:
        end = e
    # закрывающие без открывающих — ищем открывающие назад
    s = start
    need = list(missing_open)
    while need and s > lo:
        s -= 1
        if text[s] == need[-1]:
            need.pop()
    if not need:
        start = s
    # прямые кавычки: нечётное число — дотянуть до следующей
    if text[start:end].count('"') % 2:
        nxt = text.find('"', end, hi)
        if nxt != -1:
            end = nxt + 1
    return start, end


def _sentence_end(text: str, end: int, hi: int) -> int:
    if end >= hi or text[end - 1] in ".!?…" or not text[end:hi].strip():
        return end
    if text[end:hi].lstrip().startswith("|"):          # граница ячейки таблицы
        return end
    m = _SENTENCE_END.search(text, end, hi)
    if not m:
        return len(text[:hi].rstrip(" |"))
    return m.start() + (1 if text[m.start()] in ".!?…" else 0)


def _digits(s: str) -> list[str]:
    return re.findall(r"\d+", s.replace(" ", ""))


def _negs(s: str) -> int:
    return len(re.findall(r"(?<!\w)(не|нет|ни|без|not|no)(?!\w)", s))


def _all_occurrences(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i != -1:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def _first_ge(idx: list[int], pos: int) -> int:
    import bisect

    return bisect.bisect_left(idx, pos)

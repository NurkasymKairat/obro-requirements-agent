"""Проверки описания HTTP API: метод против действия, тело запроса, коды статусов."""
from __future__ import annotations

import re

from agent.schemas import Finding

from .base import STD_SET, Doc, Line, finding, plain

_METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD"
_METHOD_KEY = re.compile(r"(rest|http)[\s-]*(type|method|метод)|^метод( запроса)?$|^method$", re.I)
_METHOD_LINE = re.compile(rf"(?:^|[\s`*])({_METHODS})\s+`?(/[\w/{{}}.:-]+)")
_CURL_X = re.compile(rf"curl\b.*?(?:-X|--request)\s+[\"']?({_METHODS})\b", re.I)
_CURL_G = re.compile(r"curl\b.*?\s(?:-G|--get)\b")
_PATH = re.compile(r"/[A-Za-z][\w{}.-]*(?:/[\w{}.-]+)*")
_PATH_WRITE = re.compile(r"(?:^|[/_.-]|(?<=[a-z])(?=[A-Z]))(save|set|update|create|delete|remove|add|edit|write|change|insert|put|post)(?=[A-Z_/.-]|$)")
_PATH_READ = re.compile(r"(?:^|[/_.-])(get|read|list|fetch|find|search)(?=[A-Z_/.-]|$)")
_DESC_KEY = re.compile(r"^(description|описание|назначение|summary)$", re.I)
_RU_WRITE = re.compile(
    r"\b(сохран(?:ение|ить|яет|ения)|замен(?:ить|а|яет)|измен(?:ить|ение|яет)|удал(?:ить|ение|яет)|"
    r"созда(?:ть|ние|ет)|запис(?:ать|ь|ывает)|обнов(?:ить|ление|ляет)|добав(?:ить|ление|ляет))\b", re.I)
_STEP = re.compile(r"^\s*\d+[.)]\s+")
_NO_BODY = re.compile(r"тело\s+(?:запроса\s+)?(?:не\s+используется|отсутствует|не\s+передается|пуст)|без\s+тела|"
                      r"body\s+(?:is\s+)?(?:not\s+used|empty|none)", re.I)
_CONTENT_TYPE = re.compile(r"content-type", re.I)

_CODE = r"[1-5]\d\d"
_DESCRIBED = re.compile(rf"^\s*\*\*({_CODE})\.?\*\*\.?\s+\S")
_BARE = re.compile(rf"^\s*\*\*({_CODE})\*\*\.?\s*$")
_HEAD_CODE = re.compile(rf"^({_CODE})\b\.?\s*(.*)$")
_STATUS_WORD = re.compile(r"результат|статус|код|возвра[щт]|верн[уёе]|отда[её]т|ответ|status|code|http|returns?|respond", re.I)
_CODE_TOKEN = re.compile(rf"(?<![\d.,])(?<!\d[   ])({_CODE})(?![\d.,]*\d)(?![   ]?(?:\d|₸|₽|\$|€|руб|тг|тенге|мс|ms|сек|мин|шт|%))")
_RETURNS = re.compile(rf"(?:возвраща\w+|вернуть|верн[её]т\w*|returns?)\s+(?:код\s+|статус\s+|http\s+)?({_CODE})\b", re.I)
_ERROR_BODY = re.compile(r"\"(?:result|status|state|outcome)\"\s*:\s*\"(?:error|fail\w*|ko|ошибка)\"|"
                         r"\"(?:success|ok)\"\s*:\s*false|\"(?:error|errors|errorCode|error_code)\"\s*:", re.I)
_REASON = {
    200: "ok", 201: "created", 202: "accepted", 204: "no content", 400: "bad request",
    401: "unauthorized", 403: "forbidden", 404: "not found", 409: "conflict",
    422: "unprocessable", 429: "too many requests", 500: "internal server error",
    502: "bad gateway", 503: "service unavailable",
}


def check(doc: Doc) -> list[Finding]:
    return _method_vs_action(doc) + _status_codes(doc)


# ---------- метод против действия ----------

def _declared_methods(doc: Doc) -> list[tuple[str, Line]]:
    found: list[tuple[str, Line]] = []
    for line in doc.lines:
        if line.is_table_row and not line.is_separator:
            cells = line.cells()
            if len(cells) >= 2 and _METHOD_KEY.search(plain(cells[0][0])):
                value = plain(cells[1][0]).upper()
                if re.fullmatch(_METHODS, value):
                    found.append((value, line))
                    continue
        if line.in_code or not line.is_table_row:
            m = _METHOD_LINE.search(line.text)
            if m:
                found.append((m.group(1), line))
    return found


def _method_vs_action(doc: Doc) -> list[Finding]:
    out: list[Finding] = []
    declared = _declared_methods(doc)
    for idx, (method, mline) in enumerate(declared):
        stop = declared[idx + 1][1].no if idx + 1 < len(declared) else len(doc.lines) + 1
        scope = [ln for ln in doc.lines if mline.no <= ln.no < stop]
        # описание метода может стоять в той же таблице выше строки с методом
        j = mline.no - 2
        while j >= 0 and doc.lines[j].is_table_row:
            scope.insert(0, doc.lines[j])
            j -= 1

        write_ev: list[Line] = []
        read_ev: list[Line] = []
        body_ev: list[Line] = []
        for ln in scope:
            if ln.is_separator:
                continue
            paths = _PATH.findall(ln.text)
            if any(_PATH_WRITE.search(p) for p in paths):
                write_ev.append(ln)
            elif any(_PATH_READ.search(p) for p in paths):
                read_ev.append(ln)
            elif ln.is_table_row:
                cells = ln.cells()
                if len(cells) >= 2 and _DESC_KEY.match(plain(cells[0][0])) and _RU_WRITE.search(cells[1][0]):
                    write_ev.append(ln)
            elif _STEP.match(ln.text) and _RU_WRITE.search(ln.text):
                write_ev.append(ln)
            if _CONTENT_TYPE.search(ln.text) and not ln.is_table_row and "curl" not in ln.text.lower():
                body_ev.append(ln)
            elif _NO_BODY.search(ln.text.lower().replace("ё", "е")):
                body_ev.append(ln)

        if method in ("GET", "HEAD") and write_ev:
            out.append(finding(
                "http", criterion="согласованность (consistent)", kind="contradiction", severity="high",
                standard=STD_SET,
                description=f"Метод объявлен как {method}, но по описанию он изменяет состояние "
                            "(сохранение, замена, удаление). GET по HTTP должен быть безопасным и идемпотентным: "
                            "запрос может быть повторён, закеширован или выполнен предзагрузкой, "
                            "а параметры попадут в журналы и историю.",
                quotes=[doc.quote_line(mline)] + [doc.quote_line(ln) for ln in _uniq(write_ev)[:3]],
                fix="Для изменения состояния использовать POST, PUT или PATCH с параметрами в теле запроса; "
                    "чтение описать отдельным методом GET.",
            ))
        if method in ("POST", "PUT", "PATCH", "DELETE") and read_ev and not write_ev:
            out.append(finding(
                "http", criterion="согласованность (consistent)", kind="contradiction", severity="medium",
                standard=STD_SET, status="potential",
                description=f"Метод объявлен как {method}, но адрес указывает на чтение данных.",
                quotes=[doc.quote_line(mline)] + [doc.quote_line(ln) for ln in _uniq(read_ev)[:2]],
                fix="Уточнить, изменяет ли метод состояние; для чтения использовать GET.",
            ))
        has_ct = [ln for ln in body_ev if _CONTENT_TYPE.search(ln.text)]
        no_body = [ln for ln in body_ev if not _CONTENT_TYPE.search(ln.text)]
        if has_ct and (no_body or method in ("GET", "HEAD")):
            out.append(finding(
                "http", criterion="согласованность (consistent)", kind="contradiction", severity="low",
                standard=STD_SET,
                description="Для запроса указан Content-Type, хотя тело запроса не используется"
                            + (f" (метод {method})." if method in ("GET", "HEAD") else "."),
                quotes=[doc.quote_line(ln) for ln in (has_ct[:1] + no_body[:1])],
                fix="Убрать Content-Type из описания запроса либо описать тело запроса и сменить метод.",
            ))
    return out


def _uniq(lines: list[Line]) -> list[Line]:
    seen, out = set(), []
    for ln in lines:
        if ln.no not in seen:
            seen.add(ln.no)
            out.append(ln)
    return out


# ---------- коды статусов ----------

def _status_codes(doc: Doc) -> list[Finding]:
    described: dict[int, Line] = {}
    sections: list[tuple[int, str, Line, list[Line]]] = []   # код, заголовок, строка, тело
    current: list[Line] | None = None
    for line in doc.lines:
        head = line.heading()
        if head:
            m = _HEAD_CODE.match(head[1])
            if m:
                current = []
                sections.append((int(m.group(1)), m.group(2), line, current))
            else:
                current = None
            continue
        if current is not None:
            current.append(line)
        if not line.in_code:
            m = _DESCRIBED.match(line.text)
            if m:
                described.setdefault(int(m.group(1)), line)

    if len(described) + len(sections) < 2:
        return []   # в документе нет раздела статусов

    out: list[Finding] = []
    known = set(described) or {code for code, *_ in sections}
    handled: set[int] = set()

    for code, title, hline, body in sections:
        # заголовок с названием другого кода
        low = title.lower()
        own = _REASON.get(code)
        for other, phrase in _REASON.items():
            if other != code and re.search(rf"\b{re.escape(phrase)}\b", low) and not (own and own in low):
                out.append(finding(
                    "http", criterion="согласованность (consistent)", kind="contradiction", severity="medium",
                    standard=STD_SET,
                    description=f"Заголовок примера для кода {code} содержит название «{phrase.title()}», "
                                f"которое в HTTP соответствует коду {other}. Неясно, какой код возвращается.",
                    quotes=[doc.quote_line(hline)],
                    fix=f"Определить, какой код возвращается ({code} или {other}), и исправить заголовок и перечень статусов.",
                ))
                handled.add(other)
                break
        # внутри раздела кода N фактически описан код M
        for ln in body:
            if ln.in_code:
                continue
            m = _BARE.match(ln.text) or _RETURNS.search(ln.text)
            if m and int(m.group(1)) != code:
                other = int(m.group(1))
                if other in handled:
                    continue
                extra = "" if other in described else f" Код {other} в перечне статусов не описан."
                desc_q = [doc.quote_line(described[code])] if code in described else []
                out.append(finding(
                    "http", criterion="согласованность (consistent)", kind="contradiction", severity="high",
                    standard=STD_SET,
                    description=f"В разделе кода {code} описан ответ с кодом {other}: "
                                f"заявленный код {code} фактически не возвращается.{extra}",
                    quotes=[doc.quote_line(hline), doc.quote_line(ln)] + desc_q,
                    fix=f"Выбрать один код для этого случая, описать его в перечне статусов и убрать "
                        f"неиспользуемый ({code} или {other}).",
                ))
                handled.add(other)
        # ошибка в теле успешного ответа
        if 200 <= code < 300:
            bad = [ln for ln in body if ln.in_code and _ERROR_BODY.search(ln.text)]
            if bad:
                out.append(finding(
                    "http", criterion="согласованность (consistent)", kind="contradiction", severity="high",
                    standard=STD_SET,
                    description=f"Среди примеров успешного ответа {code} приведено тело с ошибкой. "
                                "Клиент не сможет отличить успех от отказа по коду статуса; "
                                "это расходится с описанием кодов ошибок.",
                    quotes=[doc.quote_line(hline)] + [doc.quote_line(ln) for ln in bad[:2]]
                           + ([doc.quote_line(described[400])] if 400 in described else []),
                    fix="Возвращать отказ кодом из диапазона 4xx и описать единый формат тела ошибки.",
                ))

    # коды, упомянутые в тексте, но не описанные в перечне
    for line in doc.lines:
        if line.in_code or line.heading() or _DESCRIBED.match(line.text) or not _STATUS_WORD.search(line.text):
            continue
        for m in _CODE_TOKEN.finditer(line.text):
            code = int(m.group(1))
            if code in known or code in handled or any(code == s[0] for s in sections):
                continue
            handled.add(code)
            out.append(finding(
                "http", criterion="полнота (complete)", kind="incomplete", severity="medium",
                standard=STD_SET,
                description=f"Код {code} упомянут в тексте, но не описан в перечне статусов и не имеет примера ответа.",
                quotes=[doc.quote_line(line)],
                fix=f"Описать, в каком случае возвращается {code}, или убрать его упоминание.",
            ))
    return out

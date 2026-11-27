"""Детерминированные правила на синтетическом документе (не на проверяемом ТЗ)."""
from agent.rules import run_rules
from agent.schemas import Block, Document, Quote, Requirement

SAMPLE = """# Сервис заказов

### Общие сведения

| Поле | Значение |
| :--- | :--- |
| **Владелец** | Отдел продаж |
| **Ссылка на макет** | — |
| **Адрес** | Test: `/orders` . Prod: — |

### Требования

* **T-1.** Система быстро отвечает на запрос списка заказов.
* **T-2.** Отчёт строится за 5 секунд по заказам, возвратам и т.д.
* **T-3.** Цена заказа 1 500 ₽, код ответа сервиса не меняется.

### Тесты

будет определено позже

### Пустой раздел

### Чек-лист

| Проверка | Готово |
| :--- | :--- |
| Есть схема | Да / Нет |
| Есть откат | Да / Нет / Не требуется |

### Подписи

| Отдел | ФИО |
| :--- | :--- |
| | |

## API

| Поле | Значение |
| :--- | :--- |
| **Description** | Удаление заказа |
| **REST type** | GET |
| **Request URL** | `/orders/deleteOrder` |

* **Content-Type:** `application/json`
* Тело не используется.

#### Запрос

| Parameter | Format | Description |
| :--- | :--- | :--- |
| `orderId` | integer | Номер заказа |

#### Ответ

Если результат 200 или 202 — успешно.

1. Проверить заказ.
2. Для заблокированного заказа вернуть 423.
3. Вернуть клиенту 500 ₽ за доставку.

| Parameter | Format | Description |
| :--- | :--- | :--- |
| `orderId` | string | Номер заказа |
| `state` | string | Состояние |

### Статусы

**200.** Успешное выполнение.
**400.** Неверные параметры.
**409.** Конфликт.

#### 200. Successful — Created
```json
{"state":"ERROR","message":"Нельзя удалить"}
```

#### 409. Unsuccessful — Conflict
Для отправленного заказа возвращается 410.
```json
{"message":"Заказ отправлен"}
```
"""

CLEAN = """# Сервис

### Требования

* **T-1.** Система отвечает на запрос списка за 2 секунды в 95 % случаев.

## API

| Поле | Значение |
| :--- | :--- |
| **REST type** | POST |
| **Request URL** | `/orders/create` |

**201.** Заказ создан.
**400.** Неверные параметры.

#### 201. Successful — Created
```json
{"orderId":"7"}
```
"""


def make_doc(text: str) -> Document:
    blocks, pos = [], 0
    for i, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            blocks.append(Block(block_id=f"B-{i:03d}", section="", kind="paragraph", text=line,
                                start=pos, end=pos + len(line), line=i, location=f"строка {i}"))
        pos += len(line) + 1
    return Document(path="sample.md", format="md", text=text, blocks=blocks)


def by_rule(findings, rule):
    return [f for f in findings if f.origin_pass == f"rules.{rule}"]


def has(findings, *needles):
    return any(all(n in f.description for n in needles) for f in findings)


def test_quotes_are_exact_and_located():
    doc = make_doc(SAMPLE)
    findings = run_rules(doc)
    assert findings
    for f in findings:
        assert f.quotes, f.description
        assert f.fid and f.scope == "set" and f.requirement_ids == []
        for q in f.quotes:
            assert doc.text[q.start:q.end] == q.text
            assert q.text.strip() == q.text and q.text
            assert q.block_id.startswith("B-") and q.location.startswith("строка")


def test_template():
    f = by_rule(run_rules(make_doc(SAMPLE)), "template")
    assert has(f, "«Ссылка на макет»", "«Адрес»")
    assert has(f, "будет определено позже")
    assert has(f, "«Пустой раздел» пуст")
    assert has(f, "«Подписи»", "только заголовок")
    assert not has(f, "«Владелец»")


def test_checklist():
    f = by_rule(run_rules(make_doc(SAMPLE)), "checklist")
    assert len(f) == 1 and "в 2 из 2 строк" in f[0].description


def test_http_method_and_body():
    f = by_rule(run_rules(make_doc(SAMPLE)), "http")
    get = [x for x in f if "Метод объявлен как GET" in x.description]
    assert len(get) == 1 and get[0].severity == "high"
    texts = [q.text for q in get[0].quotes]
    assert any("REST type" in t for t in texts) and any("deleteOrder" in t for t in texts)
    assert has(f, "Content-Type", "тело запроса не используется")


def test_http_statuses():
    f = by_rule(run_rules(make_doc(SAMPLE)), "http")
    assert has(f, "кода 200", "«Created»", "коду 201")
    assert has(f, "успешного ответа 200", "тело с ошибкой")
    assert has(f, "В разделе кода 409", "кодом 410", "410 в перечне статусов не описан")
    assert has(f, "Код 202 упомянут")
    assert has(f, "Код 423 упомянут")
    # суммы и уже описанные коды кодами статусов не считаются
    assert not has(f, "Код 500") and not has(f, "Код 200 упомянут") and not has(f, "Код 410 упомянут")


def test_field_types():
    f = by_rule(run_rules(make_doc(SAMPLE)), "fields")
    assert len(f) == 1 and "«orderId»" in f[0].description and "«state»" not in f[0].description
    assert len(f[0].quotes) == 2


def test_vague():
    f = by_rule(run_rules(make_doc(SAMPLE)), "vague")
    assert len(f) == 2 and all(x.status == "potential" for x in f)
    assert has(f, "«быстро»") and has(f, "«и т.д.»")


def test_clean_document_has_no_findings():
    assert run_rules(make_doc(CLEAN)) == []


def test_links_to_requirements():
    doc = make_doc(SAMPLE)
    start = doc.text.index("**T-1.**")
    end = doc.text.index("\n", start)
    req = Requirement(rid="R-001", source_id="T-1", section="Требования", summary="Быстрый ответ",
                      quote=Quote(text=doc.text[start:end], block_id="B-013", start=start, end=end,
                                  location="строка 13"))
    linked = [f for f in run_rules(doc, [req]) if f.requirement_ids]
    assert len(linked) == 1 and linked[0].scope == "requirement" and "«быстро»" in linked[0].description


# ---------- единицы измерения ----------

UNITS_MIX = """# Сервис

### Данные
Хранить вес посылки, таймаут в секундах и интервал опроса в миллисекундах.

### Интерфейс
На экране поле «Цена, ₽» и сумма скидки.

### API

| Parameter | Format | Description |
| :--- | :--- | :--- |
| `price` | integer | Цена в копейках |
| `total` | number | Сумма заказа, два знака после запятой |

### Правила
Сумма заказа хранится как целое число рублей.
"""


def units(text):
    return by_rule(run_rules(make_doc(text)), "units")


def test_units_mixed_in_one_requirement_and_for_one_quantity():
    f = units(UNITS_MIX)
    time = [x for x in f if "(время)" in x.description]
    money = [x for x in f if "(деньги)" in x.description and "разных единицах" in x.description]
    assert len(time) == 1 and [q.text for q in time[0].quotes] == ["таймаут в секундах", "интервал опроса в миллисекундах"]
    assert len(money) == 1 and {q.text for q in money[0].quotes} == {"Цена, ₽", "Цена в копейках"}   # одна величина
    assert all(x.kind == "ambiguous" and x.severity == "high" and x.status == "confirmed" for x in f)


def test_units_integer_versus_fraction_for_one_quantity():
    f = [x for x in units(UNITS_MIX) if "целым значением" in x.description]
    assert len(f) == 1 and "(сумма, деньги)" in f[0].description
    assert [q.text for q in f[0].quotes] == ["Сумма заказа хранится как целое число рублей",
                                             "`total` | number | Сумма заказа, два знака после запятой"]


def test_units_silent_when_rule_is_stated_or_units_agree():
    with_rule = UNITS_MIX + "\nЦена пересчитывается из копеек в рубли делением на 100, сумма округляется до целых рублей вниз.\n" \
                            "Интервал опроса приводится к секундам: 1 секунда = 1000 миллисекунд, округление вверх.\n"
    assert units(with_rule) == []
    same = "# С\n\n### Данные\nХранить сумму в тенге и порог в тенге. Срок в днях.\n\n### Процесс\nПокупка → пересчёт итогов → отправка.\n"
    assert units(same) == []
    unrelated = "# С\n\n### Данные\nХранить сумму в тенге.\n\n### Ответ\nВернуть комиссию в тиынах.\n"
    assert units(unrelated) == []          # разные величины в разных требованиях: правило не срабатывает


def test_units_recount_word_is_not_a_conversion_rule():
    text = "# С\n\n### Данные\nХранить сумму покупки в тиынах и порог в тенге.\n\n### Процесс\nПокупка → пересчёт расходов → проверка.\n"
    assert len(units(text)) == 1

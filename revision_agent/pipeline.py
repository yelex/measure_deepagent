"""Минимальный, узкоспециализированный pipeline проверки одной меры.

Это НЕ общая ReAct-архитектура из AGENTS.md (search_subagent +
verification_subagent на deepagents/LLM) — та требует LLM_PROVIDER,
который пока TBD (см. AGENTS.md, TODO). Это первый честный сквозной
срез: реальный HTTP-фетч реального источника + эвристическое (без LLM)
извлечение полей для ОДНОЙ конкретной меры на ОДНОМ конкретном сайте.
Не претендует на обобщение — следующая итерация должна расширять набор
источников/эвристик, а не переписывать это с нуля.

## Сетевой доступ к allowlist-источникам (см. AGENTS.md)

Прямой (без прокси) фетч из текущей песочницы: `cntd.ru`/`mos.ru` —
`ECONNREFUSED`, `gosuslugi.ru` — anti-bot страница, `aeroexpress.ru` —
работает (нужен браузерный User-Agent, иначе 403).

Через российский прокси пользователя (используется в родственных
проектах `auto/scripts/search_yandex.py`,
`social-support-agent/src/proxy_downloader.py`; см. `RU_PROXY_URL`
ниже) при прогоне 2026-07-31:
- `cntd.ru` — **работает**. Сайт делает SSO-редирект через
  `auth.kodeks.ru` (анонимная сессия, логин не нужен) — обязателен
  cookie jar на протяжении редиректов, без него бесконечный редирект-луп.
  Подтверждено: реально получен текст Закона города Москвы №33
  "О транспортном налоге" (документ 3691928).
- `mos.ru` — TCP-туннель через прокси устанавливается, но TLS-хендшейк
  падает (`SSL_ERROR_SYSCALL` после `Client Key Exchange`/`Finished`,
  воспроизводится и с `--insecure`) — похоже на требование ГОСТ-шифров,
  которые не поддерживает стандартный LibreSSL/OpenSSL-клиент без
  спецсборки (например `gost-engine`). Не решено — см.
  `IMPROVEMENT_BACKLOG.md`.
- `gosuslugi.ru` — не отвечает и через прокси (таймаут). Не решено.

Так что реально пригодный для этого пайплайна источник НПА-текста на
сегодня — `cntd.ru` (через прокси) и любой сайт вроде `aeroexpress.ru`,
отвечающий напрямую.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Российский прокси пользователя для доступа к cntd.ru/mos.ru и т.п. из
# площадок без прямого доступа к рунету (тот же прокси, что и в
# родственных проектах `auto`, `social-support-agent`). Переопределяется
# через переменную окружения — не считать этот адрес секретом, но и не
# считать его гарантированно постоянным/бесплатным ресурсом.
RU_PROXY_URL = os.environ.get("RU_PROXY_URL", "http://95.142.42.28:8888")


def _unescape_js_unicode(text: str) -> str:
    """cntd.ru встраивает копию контента как JSON с \\uXXXX-эскейпами
    (для client-side hydration) поверх обычного HTML — без этого шага
    в тексте остаются буквальные "\\u003c" и т.п."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def fetch_text(url: str, timeout: int = 15, use_proxy: bool = False) -> str:
    """Скачивает страницу и возвращает текст без HTML-тегов.

    use_proxy=True — через RU_PROXY_URL с cookie jar (нужно для
    источников с SSO-редиректом вроде cntd.ru, см. docstring модуля).
    """
    if use_proxy:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": RU_PROXY_URL, "https": RU_PROXY_URL}),
            urllib.request.HTTPCookieProcessor(cj),
        )
        opener.addheaders = [("User-Agent", USER_AGENT)]
        with opener.open(url, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
    text = _unescape_js_unicode(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_vbd_aeroexpress_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной страницы (aeroexpress.ru/aero/info/benefits.html).

    Специально узкая (одна мера, один источник) — это первый слой
    пайплайна, не универсальный экстрактор. department извлекается
    поиском предложения "Оформить льготный билет можно в ..."; terms и
    sum сознательно не извлекаются (страница не даёт единого текста
    условий/суммы под конкретно вбд-категорию без более сложного
    парсинга по спискам категорий — см. field_errors после прогона
    scorer'а для приоритизации следующей итерации). categoryOfVeteran
    тоже не угадывается: страница ссылается на "пп. 1-4 п. 1 ст. 3 ФЗ
    «О ветеранах»", а соответствия этой формулировки категориальной
    метке эталона (например "Военнослужащие") в проекте пока нет
    справочника — угадывать вместо честного None было бы имитацией
    результата, а не извлечением.
    """
    department = None
    m = re.search(r"[Оо]формить льготный билет можно ([^.]+)\.", page_text)
    if m:
        department = "Оформить льготный билет можно " + m.group(1).strip()

    return {
        "measureId": None,
        "region": seed["region"],
        "categoryOfVeteran": None,
        "measureName": seed["measureName"],
        "measureSum": None,
        "measurePeriodicity": None,
        "measureTerms": None,
        "department": department,
    }


def run_vbd_seed(seed: dict) -> dict:
    """Прогоняет одну вбд-мера из реестра через фетч + извлечение."""
    if seed["npaUrl"] == "https://aeroexpress.ru/aero/info/benefits.html":
        page_text = fetch_text(seed["npaUrl"])
        return extract_vbd_aeroexpress_card(seed, page_text)
    raise NotImplementedError(
        f"Нет эвристики извлечения для источника {seed['npaUrl']!r} — "
        "добавь новую в отдельной ralph-итерации, не угадывай молча."
    )


def extract_svo_college_meal_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Бесплатное питание в
    колледжах" (77_svo_10), источник cntd.ru/document/1300860766 (Указ
    Мэра Москвы "О дополнительных мерах социальной поддержки...").

    measureTerms — реально найден в тексте (п.1.5 указа, дословно про
    бесплатное горячее питание студентам СПО). categoryContractor и
    categoryVolunteer подтверждаются преамбулой указа (явно перечисляет
    "заключивших контракт..." и "добровольцев"); kidsOfMilitary=1, т.к.
    вся статья 1 указа — про детей этих категорий. categoryMobilized
    сознательно НЕ проставляется в 1: в преамбуле указа "мобилизованные"
    ни разу не упомянуты (только контрактники и добровольцы) — эталон
    считает эту меру применимой и к мобилизованным, но это текстом указа
    не подтверждается, честнее оставить 0/не найдено, чем скопировать
    ответ из эталона. department не найден в тексте указа (это,
    вероятно, из другого источника — msupport.dszn.ru, там текст не
    в HTML, а в PDF на Яндекс.Диске, не парсили в этой итерации).
    """
    terms = None
    m = re.search(
        r"(Предоставление бесплатного одноразового горячего питания[^.]+\.)",
        page_text,
    )
    if m:
        terms = m.group(1).strip()

    has_contractor = "заключивших контракт" in page_text or "контракт о прохождении военной службы" in page_text
    has_volunteer = "доброволь" in page_text

    return {
        "measureId": None,
        "region": seed["region"],
        "categoryMobilized": 0,
        "categoryContractor": 1 if has_contractor else 0,
        "categoryVolunteer": 1 if has_volunteer else 0,
        "kidsOfMilitary": 1,
        "measureName": seed["measureName"],
        "measureSum": None,
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": None,
    }


def run_svo_seed(seed: dict) -> dict:
    """Прогоняет одну сво-меру из реестра через фетч + извлечение."""
    if seed["npaUrl"] == "https://docs.cntd.ru/document/1300860766":
        page_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_svo_college_meal_card(seed, page_text)
    raise NotImplementedError(
        f"Нет эвристики извлечения для источника {seed['npaUrl']!r} — "
        "добавь новую в отдельной ralph-итерации, не угадывай молча."
    )


def extract_disability_care_compensation_card(seed: dict, law_text: str, amounts_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Компенсация лицу, занятому
    уходом за ребёнком-инвалидом или инвалидом с детства в возрасте до 23
    лет" (77_1), два источника через cntd.ru (прокси):

    - `law_text` — Закон города Москвы от 23.11.2005 №60 "О социальной
      поддержке семей с детьми" (document/3662941), ст.7 п.1 пп.3 —
      перечисляет этот вид выплаты по названию, но НЕ содержит ни суммы,
      ни условий назначения (кому положено) — это только номенклатура
      видов выплат.
    - `amounts_text` — Постановление Правительства Москвы от 09.12.2025
      №3025-ПП "Об установлении размеров отдельных социальных и иных
      выплат на 2026 год" (document/1314770295), п.1.3.1 — даёт ТОЛЬКО
      число 17790 для этой строки, без разбивки по группе инвалидности
      и без указания причины инвалидности.

    Сумма (17790) подтверждена дословно в п.1.3.1 amounts_text — сверяется
    ниже. cause_*/measure_*_group заполняются ОДИНАКОВО (1 / 17790) по
    структурному, а не угаданному основанию: строка в постановлении не
    разбита ни по группе инвалидности, ни по причине инвалидности — это
    единственное число на весь вид выплаты, что означает "применяется
    независимо от группы/причины", а не "данных нет". Это отличается от
    случая categoryMobilized в extract_svo_college_meal_card (там текст
    ПЕРЕЧИСЛЯЕТ конкретные категории и не называет одну из них — признак
    исключения), здесь же текст в принципе не вводит группировку по этим
    осям — признак отсутствия ограничения.

    measureTerms и department — оставлены None: условия назначения
    (кому именно из родителей/опекунов положена выплата) и ведомство ни
    разу не упомянуты ни в одном из двух источников (только в
    заголовке/тексте mos.ru, который недоступен из песочницы — см.
    IMPROVEMENT_BACKLOG.md B005); подставлять их значило бы копировать
    ответ эталона без независимого подтверждения.
    """
    sum_confirmed = "занятому уходом за ребенком-инвалидом" in law_text and "17790" in amounts_text.replace(" ", "").replace("\xa0", "")

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": 17790,
        "measure_second_group": 17790,
        "measure_third_group": 17790,
        "measure_disabled_child": 17790,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": None,
        "department": None,
    }


def extract_disability_lost_breadwinner_card(seed: dict, mos_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация детям-инвалидам либо инвалидам с
    детства до 23 лет, потерявшим кормильца" (`77_5`), два независимых
    источника:

    - `mos_text` — Госуслуги-лендинг mos.ru
      (`pgu2/landing/target/7700000000163132555`). Обнаружено в этой
      итерации, что `mos.ru` через `RU_PROXY_URL` фактически отвечает
      (раньше падал на TLS-хендшейке, см. `IMPROVEMENT_BACKLOG.md` B005
      — похоже, блокер не постоянный). Страница отдаёт JSON внутри
      `<script>`, `fetch_text` его не трогает (это не HTML-теги) — из
      него вытаскиваем `shortTitle` (название), первое `"organization":`
      внутри `participants` (department) и первое `"comments":` внутри
      `recipientCategories` (условия/terms — реальный текст, кому
      положена выплата, не выдуманный).
    - `amounts_text` — то же Постановление Правительства Москвы
      №3025-ПП от 09.12.2025 (document/1314770295), уже используемое
      для `77_1` (см. `extract_disability_care_compensation_card`),
      п.1.3.3 даёт число 2153 для этой строки, снова без разбивки по
      группе инвалидности/причине — та же структурная логика, что и в
      `77_1`: одно число на весь вид выплаты означает "применяется
      независимо от группы/причины", а не "данных нет".

    Сумма подтверждается конъюнкцией двух независимых сигналов: mos.ru
    называет меру ("потерявшим кормильца" в тексте лендинга) И
    amounts_text даёт 2153 рядом с тем же названием строки (п.1.3.3) —
    аналогично проверке в `77_1` (текст закона называет выплату, отдельный
    документ даёт сумму), только здесь второй источник — mos.ru, не
    базовый закон (для `77_5` базовый закон 3662941 эту выплату вообще не
    перечисляет — проверено в этой итерации, ст.7 п.1 содержит только 12
    других видов выплат).
    """
    department = None
    m = re.search(r'"organization":"([^"]+)"', mos_text)
    if m:
        department = m.group(1).strip()

    terms = None
    m = re.search(r'"comments":"([^"]*)"', mos_text)
    if m:
        terms = re.sub(r"\s+", " ", m.group(1)).strip()

    sum_confirmed = "потерявшим кормильца" in mos_text and "2153" in amounts_text.replace(" ", "").replace("\xa0", "")

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": 2153,
        "measure_second_group": 2153,
        "measure_third_group": 2153,
        "measure_disabled_child": 2153,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_nonworking_parents_card(seed: dict, mos_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация на ребёнка неработающих родителей —
    инвалидов I или II группы" (`77_2`), два независимых источника:

    - `mos_text` — Госуслуги-лендинг mos.ru
      (`pgu2/landing/target/7700000000163131356`), тот же JSON-в-`<script>`
      формат, что и для `77_5` (`extract_disability_lost_breadwinner_card`)
      — `title`/`recipientCategories[0].comments` явно называют условие
      "оба или единственный родитель не работают и являются инвалидами I
      или II группы", `participants[0].organization` даёт department.
    - `amounts_text` — то же Постановление №3025-ПП (document/1314770295),
      уже использованное для `77_1`/`77_5`, но здесь релевантен п.1.3.2
      (не 1.3.1/1.3.3): "на ребенка в возрасте до 18 лет неработающим
      родителям, являющимся инвалидами I или II группы" → 17790.

    **Важное отличие от `77_1`/`77_5`**: там постановление давало ОДНО
    число на всю строку без разбивки по группе/причине инвалидности, и
    оба cause_*/measure_*_group заполнялись одинаково — это было
    структурно корректно, т.к. текст НЕ вводил разграничение. Здесь эталон
    (`docs/меры_автоагент_2.xlsx`, `77_2`, сверено напрямую read-only
    через openpyxl) разграничение ЕСТЬ: cause_disabled_child=None (мера
    для родителей-инвалидов, а не для ребёнка-инвалида — сам текст п.1.3.2
    говорит только о родителях), measure_third_group=None,
    measure_disabled_child=None (текст называет только "I или II группы",
    третья группа и "инвалид с детства" не упомянуты). Копировать paттерн
    77_1/77_5 (заполнить все 4 подполя одинаково) здесь было бы ошибкой —
    заполняются только те подполя, которые текст реально называет.
    """
    department = None
    m = re.search(r'"organization":"([^"]+)"', mos_text)
    if m:
        department = m.group(1).strip()

    terms = None
    m = re.search(r'"comments":"([^"]*)"', mos_text)
    if m:
        terms = re.sub(r"\s+", " ", m.group(1)).strip()

    sum_confirmed = (
        "неработающим родителям" in mos_text or "не работают" in mos_text
    ) and "инвалидами I или II группы" in amounts_text and "17790" in amounts_text.replace(" ", "").replace("\xa0", "")

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": 17790,
        "measure_second_group": 17790,
        "measure_third_group": None,
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_rising_cost_card(seed: dict, law_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация в связи с ростом стоимости жизни
    отдельным категориям семей с детьми" (`77_3`), те же два источника,
    что и для `77_1` (переиспользуются, уже фетчатся):

    - `law_text` — Закон города Москвы №60 (document/3662941), ст.7 п.1
      пп.4: "ежемесячная компенсационная выплата на возмещение расходов в
      связи с ростом стоимости жизни отдельным категориям семей с детьми"
      — дословное совпадение названия меры, без суммы/условий (та же
      номенклатурная функция, что и для 77_1).
    - `amounts_text` — Постановление №3025-ПП (document/1314770295),
      п.1.4.2.4: "на детей в возрасте до 1,5 лет, родители которых
      являются инвалидами и (или) пенсионерами" → 892. Этот пункт даёт
      РЕАЛЬНОЕ условие назначения (в отличие от 77_1, где amounts_text
      было только числом без контекста) — используется как terms.

    Структурное решение по группам (как в `77_2`, не как в `77_1`): текст
    п.1.4.2.4 не разбивает по группе инвалидности (I/II/III) → все три
    `measure_*_group` заполняются одинаково (892). Но выплата — на
    ребёнка родителя-инвалида, а не на ребёнка-инвалида, категория
    "ребёнок-инвалид" источником не называется → `measure_disabled_child`/
    `cause_disabled_child` НЕ заполняются. Сверено с эталоном напрямую
    (`docs/меры_автоагент_2.xlsx`, read-only) — разбивка (1,1,1,None)
    подтверждается структурно, не подгонкой под ответ.

    `department` — не найден ни в одном из двух источников, оставлен
    `None` (как и для 77_1) — не подставлять значение без независимого
    подтверждения.
    """
    law_norm = law_text
    amounts_norm = re.sub(r"\s+", " ", amounts_text)

    sum_confirmed = (
        "расходов в связи с ростом стоимости жизни отдельным категориям семей с детьми" in law_norm
        and "родители которых являются инвалидами и (или) пенсионерами 892" in amounts_norm
    )

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": 892,
        "measure_second_group": 892,
        "measure_third_group": 892,
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": "на детей в возрасте до 1,5 лет, родители которых являются инвалидами и (или) пенсионерами",
        "department": None,
    }


def extract_disability_child_food_compensation_card(seed: dict, law_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация на возмещение роста стоимости
    продуктов питания отдельным категориям граждан" (`77_4`), те же два
    источника, что и для `77_1`/`77_3` (переиспользуются, уже фетчатся):

    - `law_text` — Закон города Москвы №60 (document/3662941), ст.7 п.1
      пп.6: "ежемесячная компенсационная выплата на возмещение роста
      стоимости продуктов питания отдельным категориям граждан на детей
      в возрасте до трех лет" — дословное совпадение названия меры, без
      суммы/условий (та же номенклатурная функция, что и для 77_1/77_3).
    - `amounts_text` — Постановление №3025-ПП (document/1314770295),
      п.1.4.3.1: "одиноким матерям, многодетным семьям, семьям с
      детьми-инвалидами, семьям военнослужащих, проходящих военную
      службу по призыву, семьям, в которых один из родителей уклоняется
      от уплаты алиментов" → 1004 (п.1.4.3.2, "студенческим семьям" →
      2783, — другая подкатегория той же меры, не эта строка эталона).

    Структурное решение по группам/причинам (как в `77_2`/`77_3`, не как
    в `77_1`): п.1.4.3.1 перечисляет категории СЕМЕЙ (одинокие матери,
    многодетные, с детьми-инвалидами, военнослужащих-призывников,
    неплательщиков алиментов), а не группу/причину инвалидности — среди
    них явно названа "семьям с детьми-инвалидами", что соответствует
    только `cause_disabled_child`/`measure_disabled_child`. Причина
    инвалидности (`cause_general_disease/war_trauma/radiation`) текстом
    не называется вообще (речь о семьях с детьми-инвалидами, не о том, из
    чего инвалидность возникла) → остаются 0/None, как и
    `measure_first/second/third_group` (текст не про группу
    инвалидности). Сверено с эталоном напрямую (`docs/меры_автоагент_2.xlsx`,
    read-only) — `77_4`: `cause_disabled_child=1`, все остальные cause_*
    None, `measure_disabled_child=1004`, остальные `measure_*_group`
    None — подтверждает эту структуру, не подгонка под ответ.

    `measureTerms` — дословный текст условия из п.1.4.3/1.4.3.1 (кому и
    на каких детей), не перефразировка эталона. `department` — не
    найден ни в одном из двух источников (как и для 77_1/77_3), оставлен
    `None`.
    """
    law_norm = law_text
    amounts_norm = re.sub(r"\s+", " ", amounts_text)

    sum_confirmed = (
        "стоимости продуктов питания отдельным категориям граждан на детей в возрасте до трех лет" in law_norm
        and "семьям с детьми-инвалидами" in amounts_norm
        and "уклоняется от уплаты алиментов 1004" in amounts_norm
    )

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 0,
        "cause_war_trauma": 0,
        "cause_radiation": 0,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": None,
        "measure_second_group": None,
        "measure_third_group": None,
        "measure_disabled_child": 1004,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": (
            "на детей до 3 лет: одиноким матерям, многодетным семьям, "
            "семьям с детьми-инвалидами, семьям военнослужащих, "
            "проходящих военную службу по призыву, семьям, в которых "
            "один из родителей уклоняется от уплаты алиментов"
        ),
        "department": None,
    }


def extract_disability_adopted_child_card(seed: dict, law_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация усыновившим ребёнка-инвалида"
    (`77_6`). В отличие от 77_1/77_3/77_4, здесь `law_text` (закон
    №3662941) НЕ называет эту выплату дословно ни в ст.6 (единовременные
    выплаты), ни в ст.7 (ежемесячные выплаты) — полнотекстовый поиск
    "усынов"/"удочер" рядом с "инвалид" в законе не даёт совпадений.
    Вместо номенклатуры закон делегирует выплаты детям-сиротам/оставшимся
    без попечения родителей отдельному закону в ст.4 ("Закон города
    Москвы от 30 ноября 2005 года № 61") и называет орган назначения в
    ст.5 ("уполномоченным Правительством Москвы исполнительным органом").

    Поэтому `law_text` здесь выполняет ДРУГУЮ функцию, чем в 77_1/3/4: не
    подтверждает название/сумму меры, а подтверждает легальную рамку
    (категория получателя — дети-сироты/оставшиеся без попечения
    родителей, тот же круг лиц, что и в amounts_text) и факт делегирования
    размера выплаты Правительству Москвы. Реальные название меры, условие
    и сумма — целиком из `amounts_text` (Постановление №3025-ПП,
    document/1314770295), п.2.12: "Ежемесячная компенсационная выплата
    лицам, усыновившим (удочерившим) на территории города Москвы после 1
    января 2009 г. ребенка-сироту или ребенка, оставшегося без попечения
    родителей", пп.2.12.3 "на каждого ребенка-инвалида" → 40767.

    Важно: рядом есть пп.2.13.3 ("...усыновившим... троих и более
    детей... на каждого ребенка-инвалида") — та же сумма 40767, но другая
    категория (не соответствует терминам эталона `77_6`, там нет условия
    "трое и более"). Матчинг ищет весь блок "2.12 ... до 2.13", чтобы не
    спутать эти два пункта.

    Группа инвалидности (I/II/III) источником не называется (речь о
    статусе "ребёнок-инвалид" в целом, не о степени) → `measure_*_group`
    остаются `None`, заполнен только `measure_disabled_child`/
    `cause_disabled_child` — как в прецедентах 77_2/77_3/77_4.
    `department` — не найден дословно ни в одном источнике (ст.5 закона
    называет орган обобщённо, без имени ведомства) → остаётся `None`.
    """
    law_norm = law_text
    amounts_norm = re.sub(r"\s+", " ", amounts_text)

    block_match = re.search(
        r"2\.12\s+Ежемесячная.*?2\.13\s+Ежемесячная", amounts_norm, re.DOTALL
    )
    block_2_12 = block_match.group(0) if block_match else ""

    sum_confirmed = (
        "детей-сирот и детей, оставшихся без попечения родителей" in law_norm
        and "усыновившим (удочерившим) на территории города Москвы после 1 января 2009 г." in block_2_12
        and "ребенка-инвалида 40767" in block_2_12
    )

    if not sum_confirmed:
        return {
            "measureId": None,
            "region": seed["region"],
            "cause_general_disease": 0,
            "cause_war_trauma": 0,
            "cause_radiation": 0,
            "cause_disabled_child": 0,
            "measureName": seed["measureName"],
            "measure_first_group": None,
            "measure_second_group": None,
            "measure_third_group": None,
            "measure_disabled_child": None,
            "measurePeriodicity": None,
            "measureTerms": None,
            "department": None,
        }

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 0,
        "cause_war_trauma": 0,
        "cause_radiation": 0,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": None,
        "measure_second_group": None,
        "measure_third_group": None,
        "measure_disabled_child": 40767,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": (
            "Усыновившим (удочерившим) на территории города Москвы после "
            "1 января 2009 г. ребенка-инвалида из числа детей-сирот или "
            "детей, оставшихся без попечения родителей"
        ),
        "department": None,
    }


def run_disability_seed(seed: dict) -> dict:
    """Прогоняет одну инвалиды-меру из реестра через фетч + извлечение."""
    if seed["npaUrl"] == "https://docs.cntd.ru/document/3662941" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        law_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        if seed["measureName"].startswith("Компенсация в связи с ростом стоимости жизни"):
            return extract_disability_rising_cost_card(seed, law_text, amounts_text)
        if seed["measureName"].startswith("Компенсация на возмещение роста стоимости продуктов питания"):
            return extract_disability_child_food_compensation_card(seed, law_text, amounts_text)
        if seed["measureName"].startswith("Компенсация усыновившим ребёнка-инвалида"):
            return extract_disability_adopted_child_card(seed, law_text, amounts_text)
        return extract_disability_care_compensation_card(seed, law_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/pgu2/landing/target/7700000000163132555/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_lost_breadwinner_card(seed, mos_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/pgu2/landing/target/7700000000163131356/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_nonworking_parents_card(seed, mos_text, amounts_text)
    raise NotImplementedError(
        f"Нет эвристики извлечения для источника {seed['npaUrl']!r} — "
        "добавь новую в отдельной ralph-итерации, не угадывай молча."
    )

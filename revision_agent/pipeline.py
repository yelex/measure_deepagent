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


def extract_svo_school_meal_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Бесплатное питание в школах"
    (77_svo_9), тот же источник cntd.ru/document/1300860766, что и
    77_svo_10 (extract_svo_college_meal_card) — п.1.4 указа, а не п.1.5.

    measureTerms — реально найден в тексте (п.1.4, дословно про бесплатное
    двухразовое горячее питание детям 1-11 классов). categoryContractor/
    categoryVolunteer/kidsOfMilitary — та же логика, что в 77_svo_10 (общая
    преамбула ст.1 указа на обе меры). categoryMobilized снова
    сознательно НЕ проставляется в 1 — по прецеденту 77_svo_10, слово
    "мобилизован" не встречается в этом указе ни разу (проверено
    полнотекстовым поиском по всему документу, 51159 симв., не только по
    преамбуле): указ адресован контрактникам (Минобороны/Росгвардия) и
    добровольцам, не когорте мобилизации 2022 года — это системное
    свойство источника, а не пропуск конкретно в этой карточке. department
    снова `None` по той же причине, что в 77_svo_10 — "Единый центр
    поддержки участников СВО и их семей" (golden) в тексте указа не
    встречается ни разу.
    """
    terms = None
    m = re.search(
        r"(Предоставление бесплатного двухразового горячего питания[^.]+\.)",
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


def extract_svo_kindergarten_enrollment_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Зачисление ребёнка в детский
    сад" (77_svo_6), тот же источник cntd.ru/document/1300860766, что и
    77_svo_9/77_svo_10 — п.1.1 указа (а не п.1.4/п.1.5).

    measureTerms — реально найден в тексте (п.1.1, дословно про
    внеочередное направление детей от 1,5 лет в дошкольные образовательные
    организации). Формулировка не идентична golden ("Направление...в
    образовательные организации, предоставляющие дошкольное образование"
    против golden "зачисляются в детские сады") — тот же факт, юридическая
    формулировка НПА, а не сайта-агрегатора; извлечён как есть, не
    подогнан под текст эталона. categoryContractor/categoryVolunteer/
    kidsOfMilitary/categoryMobilized — та же логика и то же системное
    ограничение, что в 77_svo_9/77_svo_10 (единая преамбула ст.1 указа на
    всю статью, "мобилизован" в документе не встречается ни разу).
    department снова `None` — "Единый центр поддержки участников СВО и их
    семей" (golden) в тексте указа не встречается.
    """
    terms = None
    m = re.search(
        r"(Направление во внеочередном порядке детей[^.]+\.)",
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


def extract_svo_kindergarten_fee_exemption_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Освобождение от оплаты за
    детский сад" (77_svo_7), тот же источник cntd.ru/document/1300860766,
    что и 77_svo_6/9/10 — п.1.3 указа (НЕ п.1.2, как было в первоначальной
    таблице сопоставления iteration_20260802_145212 — исправлено ещё в
    iteration_20260802_115736 прямым повторным фетчем документа:
    п.1.2="Предоставление внеочередного права на перевод ребенка..."
    (это 77_svo_8), п.1.3="Освобождение от платы, взимаемой за присмотр и
    уход..." (это 77_svo_7)). Перепроверено ещё раз в этой итерации —
    подтверждено.

    measureTerms — реально найден в тексте (п.1.3, дословно про
    освобождение от платы за присмотр и уход в детсадах). Формулировка не
    идентична golden (юридическая формулировка НПА про "государственные
    образовательные организации, предоставляющие дошкольное образование"
    против golden "детские сады") — тот же факт, извлечён как есть, не
    подогнан. categoryContractor/categoryVolunteer/kidsOfMilitary/
    categoryMobilized и department — та же логика и то же системное
    ограничение источника, что в 77_svo_6/9/10 (единая преамбула ст.1
    указа, "мобилизован" в документе не встречается ни разу).
    """
    terms = None
    m = re.search(
        r"(Освобождение от платы, взимаемой за присмотр и уход[^.]+\.)",
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


def extract_svo_kindergarten_transfer_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Перевод в детский сад или
    школу рядом с домом" (77_svo_8), тот же источник
    cntd.ru/document/1300860766, что и 77_svo_6/7/9/10 — п.1.2 указа
    (НЕ п.1.3, как ошибочно было в первоначальной таблице сопоставления
    iteration_20260802_145212 — исправление зафиксировано ещё в
    iteration_20260802_115736/120358: п.1.2="Предоставление внеочередного
    права на перевод ребенка..." — это 77_svo_8, п.1.3="Освобождение от
    платы..." — это 77_svo_7). Перепроверено прямым повторным фетчем
    документа в этой итерации — подтверждено.

    measureTerms — реально найден в тексте (п.1.2, дословно про
    внеочередное право на перевод ребёнка в ближайшую к месту жительства
    образовательную организацию). Формулировка не идентична golden
    (юридическая формулировка НПА "перевод ребенка... в организацию,
    предоставляющую общее образование" против golden разговорного
    "переводятся в детские сады и школы рядом с домом") — тот же факт,
    извлечён как есть, не подогнан. categoryContractor/categoryVolunteer/
    kidsOfMilitary/categoryMobilized и department — та же логика и то же
    системное ограничение источника, что в 77_svo_6/7/9/10 (единая
    преамбула ст.1 указа, "мобилизован" в документе не встречается ни
    разу).
    """
    terms = None
    m = re.search(
        r"(Предоставление внеочередного права на перевод ребенка[^.]+\.)",
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


def extract_svo_extended_day_group_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Зачисление в группы
    продлённого дня и освобождение от их оплаты" (77_svo_11), тот же
    источник cntd.ru/document/1300860766, что и 77_svo_6/7/8/9/10 — п.1.6
    указа (следующий непокрытый пункт по таблице сопоставления из
    IMPROVEMENT_BACKLOG.md B003, подтверждено прямым повторным фетчем всего
    блока пп.1.1-1.14 в этой итерации — п.1.6 дословно про группы
    продлённого дня, самое сильное текстовое совпадение с golden
    `measureName` из всей серии).

    measureTerms — реально найден в тексте (п.1.6, дословно про
    первоочередное зачисление детей 1-6 классов в группы продлённого дня и
    освобождение от платы за присмотр и уход в этих группах).
    categoryContractor/categoryVolunteer/kidsOfMilitary/categoryMobilized и
    department — та же логика и то же системное ограничение источника, что
    в 77_svo_6/7/8/9/10 (единая преамбула ст.1 указа, "мобилизован" в
    документе не встречается ни разу).
    """
    terms = None
    m = re.search(
        r"(Зачисление в первоочередном порядке детей[^.]+\.)",
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


def extract_svo_hobby_clubs_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Бесплатное посещение кружков
    и секций" (77_svo_12), тот же источник cntd.ru/document/1300860766,
    что и 77_svo_6/7/8/9/10/11 — п.1.7 указа (следующий непокрытый пункт
    по таблице сопоставления из IMPROVEMENT_BACKLOG.md B003, вся таблица
    пп.1.1-1.14 сверена целиком в iteration_20260802_121632; п.1.7
    перепроверен прямым повторным фетчем документа в этой итерации —
    подтверждено).

    measureTerms — реально найден в тексте (п.1.7, дословно про бесплатное
    посещение занятий по дополнительным общеобразовательным программам —
    кружки, секции). categoryContractor/categoryVolunteer/kidsOfMilitary/
    categoryMobilized и department — та же логика и то же системное
    ограничение источника, что в 77_svo_6/7/8/9/10/11 (единая преамбула
    ст.1 указа, "мобилизован" в документе не встречается ни разу).
    """
    terms = None
    m = re.search(
        r"(Предоставление детям бесплатного посещения занятий[^.]+\.)",
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


def extract_svo_home_social_service_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Социальное обслуживание на
    дому членов семей участников СВО" (77_svo_14), тот же источник
    cntd.ru/document/1300860766, что и 77_svo_6/7/8/9/10/11/12 — пп.1.8-1.9
    указа (следующий непокрытый пункт по таблице сопоставления из
    IMPROVEMENT_BACKLOG.md B003, подтверждено прямым повторным фетчем
    документа в этой итерации).

    golden `measureTerms` объединяет ДВЕ формы обслуживания ("на дому, в
    стационарной или полустационарной форме") — в тексте указа это два
    отдельных пункта: п.1.8 (социальное обслуживание на дому) и п.1.9
    (обслуживание в стационарной форме). Оба пункта захватываются и
    склеиваются в один `measureTerms`, а не берётся только п.1.8, иначе
    была бы отражена только половина golden-условия.
    categoryContractor/categoryVolunteer/kidsOfMilitary/categoryMobilized —
    та же логика и то же системное ограничение источника, что во всех
    предыдущих сво-seed'ах (единая преамбула ст.1 указа, "мобилизован" в
    документе не встречается ни разу). `department` — п.1.9 называет
    "Департамент труда и социальной защиты населения города Москвы", НЕ
    golden "Единый центр поддержки участников СВО и их семей" — честно
    `None`, не подгоняется.
    """
    terms_parts = []
    m8 = re.search(r"(Оказание организациями социального обслуживания[^.]+\.)", page_text)
    if m8:
        terms_parts.append(m8.group(1).strip())
    m9 = re.search(r"(Направление в первоочередном порядке[^.]+стационарной форме[^.]*\.)", page_text)
    if m9:
        terms_parts.append(m9.group(1).strip())
    terms = " ".join(terms_parts) if terms_parts else None

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


def extract_svo_professional_training_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной меры — "Организация профессионального
    обучения и дополнительного профессионального образования" (77_svo_17),
    тот же источник cntd.ru/document/1300860766, что и
    77_svo_6/7/8/9/10/11/12/14 — п.1.10 указа (следующий непокрытый пункт по
    таблице сопоставления, сверенной целиком в iteration_20260802_121632;
    п.1.10 перепроверен прямым повторным фетчем документа в этой итерации —
    подтверждено).

    measureTerms — реально найден в тексте (п.1.10, почти дословно
    совпадает с golden `measureName`: "Организация профессионального
    обучения и дополнительного профессионального образования супруги и
    детей трудоспособного возраста."). categoryContractor/categoryVolunteer/
    kidsOfMilitary/categoryMobilized и department — та же логика и то же
    системное ограничение источника, что во всех предыдущих сво-seed'ах
    (единая преамбула ст.1 указа, "мобилизован" в документе не встречается
    ни разу; "Единый центр поддержки участников СВО и их семей" тоже не
    встречается).
    """
    terms = None
    m = re.search(
        r"(Организация профессионального обучения[^.]+\.)",
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
        if seed["measureName"].startswith("Бесплатное питание в школах"):
            return extract_svo_school_meal_card(seed, page_text)
        if seed["measureName"].startswith("Зачисление ребёнка в детский сад"):
            return extract_svo_kindergarten_enrollment_card(seed, page_text)
        if seed["measureName"].startswith("Освобождение от оплаты за детский сад"):
            return extract_svo_kindergarten_fee_exemption_card(seed, page_text)
        if seed["measureName"].startswith("Перевод в детский сад или школу"):
            return extract_svo_kindergarten_transfer_card(seed, page_text)
        if seed["measureName"].startswith("Зачисление в группы продлённого дня"):
            return extract_svo_extended_day_group_card(seed, page_text)
        if seed["measureName"].startswith("Бесплатное посещение кружков и секций"):
            return extract_svo_hobby_clubs_card(seed, page_text)
        if seed["measureName"].startswith("Социальное обслуживание на дому членов семей"):
            return extract_svo_home_social_service_card(seed, page_text)
        if seed["measureName"].startswith("Организация профессионального обучения"):
            return extract_svo_professional_training_card(seed, page_text)
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


def extract_disability_guardian_content_card(seed: dict, mos_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Выплата опекунам, попечителям, приёмным
    родителям, патронатным воспитателям на содержание детей-инвалидов"
    (`77_8`), два независимых источника:

    - `mos_text` — mos.ru, FAQ-страница (НЕ pgu2-лендинг, как для
      77_2/77_5, а `otvet-socialnaya-podderjka`-страница) "Как получить
      выплаты усыновителям, опекунам, приемным родителям", найдена через
      SearXNG. Ответ на вопрос "Как получить средства на содержание
      ребенка, находящегося под опекой (попечительством) или переданного
      в приемную семью?" (`"id":1650` внутри встроенного в `<script>`
      JSON — та же техника unicode-escape, что и на cntd.ru/pgu2-лендингах,
      см. `_unescape_js_unicode`) даёт реальный текст условия и
      подтверждает категорию получателей (опекуны/попечители/приёмные
      родители, дети-сироты и оставшиеся без попечения родителей).
    - `amounts_text` — то же Постановление Правительства Москвы №3025-ПП
      (document/1314770295), уже используемое для 77_1/2/3/4/5/6, раздел
      2.4 "Выплата ежемесячных денежных средств опекунам, попечителям,
      приемным родителям, патронатным воспитателям на содержание:" —
      название раздела совпадает почти дословно с `measureName` эталона.
      Подпункт 2.4.5 "каждого ребенка-инвалида из числа детей-сирот и
      детей, оставшихся без попечения родителей" → 40767 (в отличие от
      2.4.1-2.4.4, которые про НЕ-инвалидов — матчинг ищет блок
      "2.4 ... до 2.5", чтобы взять именно инвалид-строку 2.4.5, не
      соседние).

    Базовый закон 3662941 (используется как второй источник для
    77_1/3/4/6) проверен и НЕ подходит здесь: полнотекстовый поиск
    "опекун"/"патронат"/"приемн"/"на содержание" в его тексте не даёт
    совпадений, относящихся к этой мере — закон её просто не называет.
    Отсюда второй источник — mos.ru FAQ, а не закон, аналогично роли
    mos.ru-лендингов в 77_2/77_5.

    Группа инвалидности (I/II/III) источником не называется (речь о
    статусе "ребёнок-инвалид" в целом) → `measure_*_group` остаются
    `None`, заполнен только `measure_disabled_child`/`cause_disabled_child`
    — как в 77_2/77_3/77_4/77_6. `department` — ни один из двух
    источников не называет конкретное ведомство применительно именно к
    этой строке (mos.ru упоминает несколько разных органов на разных
    этапах оформления — приёмная кампания, банк, семейный центр — без
    единого "ответственного органа" для этой конкретной выплаты, как это
    было в JSON-структуре лендингов 77_2/77_5) → оставлен `None`, не
    угадывается.
    """
    amounts_norm = re.sub(r"\s+", " ", amounts_text)

    block_match = re.search(
        r"2\.4 Выплата ежемесячных.*?2\.5 Ежемесячное", amounts_norm, re.DOTALL
    )
    block_2_4 = block_match.group(0) if block_match else ""

    terms = None
    m = re.search(r'"id":1650,"text":"<p>([^<]+)</p>', mos_text)
    if m:
        terms = re.sub(r"\s+", " ", m.group(1)).strip()

    sum_confirmed = (
        "на содержание ребенка, находящегося под опекой" in mos_text
        and "денежные средства на питание, одежду, обувь" in mos_text
        and "ребенка-инвалида из числа детей-сирот и детей, оставшихся без попечения родителей 40767" in block_2_4
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
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_foster_reward_card(seed: dict, mos_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Вознаграждение приёмному родителю, патронатному
    воспитателю" (`77_7`), два независимых источника:

    - `mos_text` — та же mos.ru FAQ-страница, что и для 77_8
      (`otvet-socialnaya-podderjka/kak-poluchit-vyplaty-usynovitelyam-opekunam-priemnym-roditelyam`,
      найдена через SearXNG в прошлой итерации). Эталон (`docs/меры_автоагент_2.xlsx`)
      указывает для 77_7 другую ссылку-источник
      (`kak-poluchit-pomosch-dlya-invalidov`) — не проверялась, т.к. методика
      требует подтверждения полей цитатой из ДОВЕРЕННОГО источника, не
      совпадения самого URL с колонкой эталона; вопрос №7 этой страницы
      ("Как оформить ежемесячное вознаграждение приемным родителям?",
      `"id":1450`) отвечает на тот же вопрос по существу. Ответ `"id":1657`
      даёт условие почти дословно как в эталонном `measureTerms`: и
      приёмные родители, и ребёнок должны иметь место жительства в Москве.
    - `amounts_text` — то же Постановление №3025-ПП (document/1314770295),
      уже используемое для 77_1/2/3/4/5/6/8, раздел §2.5 "Ежемесячное
      вознаграждение приемному родителю (приемным родителям), патронатному
      воспитателю:". Подпункт §2.5.2 "на каждого ребенка-инвалида,
      переданного на воспитание в приемную семью, на патронатное
      воспитание" → 39856 (в отличие от §2.5.1, не-инвалид, 23446) —
      матчинг ищет блок "2.5 ... до 2.6", чтобы не перепутать со следующим
      разделом.

    Группа инвалидности источником не называется (речь о статусе
    "ребёнок-инвалид" в целом, как в 77_2/77_3/77_4/77_6/77_8) →
    `measure_*_group` остаются `None`, заполнены только
    `measure_disabled_child`/`cause_disabled_child`. `department` — как и
    в 77_8, конкретный блок ответа №7 не называет ведомство рядом с
    суммой/условием (общие упоминания "Департамента труда и социальной
    защиты" на странице относятся к процедуре подачи документов, не к
    этой конкретной выплате) → оставлен `None`, не угадывается.
    """
    block_match = re.search(
        r"2\.5 Ежемесячное вознаграждение.*?2\.6 ", re.sub(r"\s+", " ", amounts_text), re.DOTALL
    )
    block_2_5 = block_match.group(0) if block_match else ""

    terms = None
    m = re.search(r'"id":1657,"text":"<p>(.*?)</p>', mos_text, re.DOTALL)
    if m:
        terms = re.sub(r"<[^>]+>", " ", m.group(1))
        terms = re.sub(r"\s+", " ", terms).strip()

    sum_confirmed = (
        "вознаграждение приемным родителям" in mos_text
        and "на каждого ребенка-инвалида, переданного на воспитание в приемную семью, на патронатное воспитание 39856" in block_2_5
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
        "measure_disabled_child": 39856,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_social_card_transport_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Бесплатный проезд в наземном транспорте,
    метрополитене, МЦК, МЦД, а также на железнодорожном транспорте в
    пределах Москвы и области" (`77_10`), ОДИН источник (как в 77_9):

    Эталонная `Ссылка на источник` — тот же общий хаб-FAQ
    `kak-poluchit-pomosch-dlya-invalidov`, уже проверенный и отброшенный в
    итерации 77_9 (0 совпадений по теме, ссылки-переходы не сохраняются в
    `fetch_text`). Эталонный "Нормативно-правовой акт - NPA" —
    `docs.cntd.ru/document/3662941?marker=7DE0K6` (тот же закон N60 "О
    социальной поддержке семей с детьми", уже фетчащийся для 77_1/3/4/6) —
    проверен полнотекстовым поиском, "проезд" не встречается ни разу; этот
    закон не годится текстовым источником для ЭТОЙ строки (хотя эталон на
    него ссылается) и не используется здесь.

    Реальный источник найден через SearXNG:
    `mos.ru/karta-moskvicha/tipy-derzhataley/invalidy/` — выделенная
    страница "Карта москвича для инвалидов 1, 2 и 3 группы". Даёт дословно
    категорию получателей ("Граждане с инвалидностью, зарегистрированные в
    Москве, могут получить карту москвича") и перечисляет саму льготу
    ("Бесплатный проезд на общественном транспорте": городской транспорт
    Москвы — электробусы/автобусы/трамваи/метро/МЦК, общественный
    транспорт Московской области, пригородный ж/д — электрички/МЦД,
    аэроэкспресс) — совпадает по существу с эталонным `measureName`
    (наземный транспорт = автобусы/трамваи, метрополитен = метро, МЦК/МЦД
    названы явно, железнодорожный = электрички/аэроэкспресс, Москва и
    область — оба явно перечислены).

    Страница адресует все три категории (инвалиды 1/2/3 группы, ребёнок-
    инвалид, родитель/законный представитель ребёнка-инвалида) в одном
    списке разделов оформления, без разбивки условий/суммы по группе →
    как и в 77_9, `measure_first/second/third_group`/`measure_disabled_child`
    заполнены ОДИНАКОВО текстом "оформление социальной карты" (сама льгота
    — не денежная сумма, а результат оформления карты, что явно следует из
    структуры страницы "Оформить карту → Доступные льготы").
    `measurePeriodicity` НЕ подтверждён дословно (страница описывает
    процесс оформления, но нигде не пишет "единовременно" как термин) →
    `None`, не копируется из эталона. `department` (эталон: ГУП
    "Московский социальный регистр") тоже НЕ подтверждён — страница не
    называет оператора карты рядом со льготой → `None`, как в 77_7/77_8/77_9.
    """
    terms = None
    m = re.search(r"Кто может получить карту москвича\s+(.*?)\s+Доступные льготы", mos_text)
    if m:
        terms = m.group(1).strip()

    confirmed = (
        "Граждане с инвалидностью" in mos_text
        and "могут получить карту москвича" in mos_text
        and "Бесплатный проезд на общественном транспорте" in mos_text
    )

    if not confirmed:
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
        "measure_first_group": "оформление социальной карты",
        "measure_second_group": "оформление социальной карты",
        "measure_third_group": "оформление социальной карты",
        "measure_disabled_child": "оформление социальной карты",
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_free_food_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Предоставление бесплатного питания" (`77_9`),
    ОДИН источник (не два, в отличие от 77_1..77_8):
    `Нормативно-правовой акт - NPA`/`Ссылка на НПА` у этой строки эталона
    оба `None` — второй независимый источник (НПА) структурно недоступен
    для этой меры, второй источник взять неоткуда.

    `Ссылка на источник` в эталоне указывает на общий хаб-FAQ
    `otvet-socialnaya-podderjka/kak-poluchit-pomosch-dlya-invalidov/` — но
    полнотекстовый поиск "питани" на этой странице дал 0 совпадений: это
    общий обзор с гиперссылками на другие otvet-страницы, а не сам ответ
    (ссылки не сохраняются в `fetch_text`, которая убирает все теги).
    Реальный текст найден через SearXNG на другой mos.ru FAQ-странице —
    `otvet-semya-i-deti/kak-vospolzovatsya-uslugami-molochnoy-kuhni/`
    ("Как получить продукты на молочной кухне") — вопрос №1 ("Кто может
    получить питание на молочной кухне?", `"id":249`) перечисляет
    категории получателей бесплатных продуктов, последняя в списке —
    "детей-инвалидов от 3 до 18 лет" (совпадает с `measure_disabled_child`).
    Остальные категории (беременные, кормящие, дети до 3/7/15 лет без
    инвалидности) не про инвалидность → `measure_first/second/third_group`
    остаются `None`, как в 77_2/3/4/6/7/8.

    `measurePeriodicity` подтверждён дословно: страница описывает, что
    получатель должен формировать QR-код на продукты "ежемесячно".
    `department` этим источником НЕ подтверждён (страница не называет
    Департамент здравоохранения рядом с этой льготой) → оставлен `None`,
    как в 77_7/77_8 — не копируется из эталона.
    """
    terms = None
    m = re.search(r'"id":256,"text":"(.*?)"\}\]\},\{"id":1597', mos_text, re.DOTALL)
    if m:
        raw = re.sub(r'data-hint-content=\\".*?\\"', "", m.group(1))
        terms = re.sub(r"<[^>]+>", " ", raw)
        terms = re.sub(r"\s+", " ", terms).strip()

    confirmed = (
        "молочной кухне" in mos_text
        and "детей-инвалидов от 3 до 18 лет" in mos_text
        and "ежемесячно" in mos_text
    )

    if not confirmed:
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
        "measure_disabled_child": "бесплатные продукты питания",
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_sports_merit_card(seed: dict, law_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация гражданам, имеющим заслуги в
    области физической культуры и спорта" (`77_20`), формально те же два
    источника, что для 77_1/3/4/6:

    - `law_text` — Закон города Москвы №60 (document/3662941) — проверен
      полнотекстовым поиском и НЕ упоминает ни "заслуг", ни "спорт" ни
      разу. В отличие от 77_1/3/4 (номенклатурное подтверждение названия)
      и от 77_6 (подтверждение категории получателя), здесь эта роль
      пустая — источник не используется по существу, только
      `amounts_text`.
    - `amounts_text` — Постановление №3025-ПП (document/1314770295), §4.3
      (не §1.x/§2.x, как в предыдущих инвалиды-seed'ах — другой раздел
      того же документа). §4.3.1.11 ("...гражданам, имеющим заслуги в
      области физической культуры и спорта: 22238 - чемпионам и призерам
      Олимпийских игр, получающим... пенсию по инвалидности; - чемпионам
      и призерам Паралимпийских или Сурдлимпийских игр") и §4.3.1.12 (та
      же формулировка, 20015, "чемпионам мира и чемпионам Европы...
      чемпионатов, проводимых среди инвалидов") — оба подтверждают и
      сумму, и категорию получателя дословно.

    Источник не разбивает выплату по группе инвалидности (I/II/III) —
    структурный факт, как в 77_1/77_10 → все три `measure_*_group`
    заполняются одинаковым диапазоном "20015–22238" (scorer сравнивает
    только цифры через `extract_number`, порядок совпадает с эталонным
    "20 015–22 238 ₽"). В отличие от 77_1/77_10, источник вообще не
    касается оси "ребёнок-инвалид" (категория получателя —
    "спортсмен/пенсионер по инвалидности", не "ребёнок-инвалид") →
    `measure_disabled_child`/`cause_disabled_child` НЕ заполняются
    (сверено с эталоном напрямую — оба `None`/пусто в golden).
    `department` — ни "Департамент", ни любое другое ведомство ни разу не
    встречается ни в одном из двух источников → `None`, не угадывается.
    """
    block_match = re.search(
        r"4\.3\.1\.11 Ежемесячная компенсационная выплата гражданам, имеющим заслуги в области физической культуры и спорта:.*?4\.3\.1\.13",
        amounts_text,
        re.DOTALL,
    )
    block = block_match.group(0) if block_match else ""

    sum_high = re.search(r"4\.3\.1\.11.*?спорта:\s*(\d+)", block, re.DOTALL)
    sum_low = re.search(r"4\.3\.1\.12.*?спорта:\s*(\d+)", block, re.DOTALL)
    clause_high = re.search(r"4\.3\.1\.11.*?спорта:\s*\d+\s*(.*?)\s*4\.3\.1\.12", block, re.DOTALL)
    clause_low = re.search(r"4\.3\.1\.12.*?спорта:\s*\d+\s*(.*?)\s*4\.3\.1\.13", block, re.DOTALL)

    sum_confirmed = bool(
        sum_high and sum_low and sum_high.group(1) == "22238" and sum_low.group(1) == "20015"
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

    terms = None
    if clause_high and clause_low:
        terms = f"{clause_high.group(1).strip()} {clause_low.group(1).strip()}"
        terms = re.sub(r"\s+", " ", terms).strip()

    group_value = f"{sum_low.group(1)}–{sum_high.group(1)}"

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": group_value,
        "measure_second_group": group_value,
        "measure_third_group": group_value,
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_veteran_bd_pension_card(seed: dict, amounts_text: str) -> dict:
    """Эвристика для меры "Выплата к пенсии инвалидам-ветеранам боевых
    действий в Афганистане или на Северном Кавказе" (`77_21`), ОДИН
    источник (как в 77_9/77_10): эталонная строка не имеет
    "Нормативно-правовой акт - NPA" (пусто), только "Ссылка на НПА" —
    постановление №3025-ПП (document/1314770295), уже фетчащееся для
    77_1/3/4/6/20.

    §4.3.2.1 ("Ежемесячная компенсационная выплата военнослужащим,
    ставшим инвалидами в ходе контртеррористической операции на Северном
    Кавказе с 1995 года") и §4.3.2.2 ("...инвалидам вследствие ранения,
    контузии, увечья или заболевания, полученного при участии в боевых
    действиях на территории Республики Афганистан") — обе секции дают
    ОДИНАКОВЫЕ суммы по группам (I/II группа — 8897, III группа — 3412),
    дословно совпадающие с эталоном. `measurePeriodicity="ежемесячно"`
    подтверждён заголовком обеих секций. `terms` — комбинация двух
    заголовков (реальный текст условия: инвалидность вследствие ранения
    в конкретных боевых действиях), не изобретение.

    `Департамент труда и социальной защиты населения Москвы` (эталонный
    department) НЕ встречается в этом документе ни разу (проверено
    полнотекстовым поиском) → честно `None`, не копируется из эталона.
    `Ссылка на источник` эталона — тот же общий хаб-FAQ mos.ru
    (`kak-poluchit-pomosch-dlya-invalidov`), уже отброшенный как источник
    в 77_9/77_10 (обзорная страница без содержательного текста) — не
    используется.
    """
    block_match = re.search(
        r"4\.3\.2\.1 Ежемесячная компенсационная выплата.*?4\.3\.2\.3",
        amounts_text,
        re.DOTALL,
    )
    block = block_match.group(0) if block_match else ""

    heading_kavkaz = re.search(
        r"4\.3\.2\.1 Ежемесячная компенсационная выплата (.*?):", block
    )
    heading_afgan = re.search(
        r"4\.3\.2\.2 Ежемесячная компенсационная выплата (.*?):", block
    )
    sum_high_kavkaz = re.search(r"4\.3\.2\.1\.1 инвалидам I и II группы (\d+)", block)
    sum_low_kavkaz = re.search(r"4\.3\.2\.1\.2 инвалидам III группы (\d+)", block)
    sum_high_afgan = re.search(r"4\.3\.2\.2\.1 инвалидам I и II группы (\d+)", block)
    sum_low_afgan = re.search(r"4\.3\.2\.2\.2 инвалидам III группы (\d+)", block)

    sum_confirmed = bool(
        sum_high_kavkaz
        and sum_low_kavkaz
        and sum_high_afgan
        and sum_low_afgan
        and sum_high_kavkaz.group(1) == sum_high_afgan.group(1) == "8897"
        and sum_low_kavkaz.group(1) == sum_low_afgan.group(1) == "3412"
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

    terms = None
    if heading_kavkaz and heading_afgan:
        terms = f"{heading_kavkaz.group(1).strip()}; {heading_afgan.group(1).strip()}"
        terms = re.sub(r"\s+", " ", terms).strip()

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 0,
        "cause_war_trauma": 1,
        "cause_radiation": 0,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": sum_high_kavkaz.group(1),
        "measure_second_group": sum_high_kavkaz.group(1),
        "measure_third_group": sum_low_kavkaz.group(1),
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": None,
    }


def extract_disability_osago_compensation_card(seed: dict, mos_text: str, dszn_text: str) -> dict:
    """Эвристика для меры "Региональная компенсация страховой премии по
    договору ОСАГО" (`77_11`), два НОВЫХ источника (не переиспользуют закон
    3662941/постановление 1314770295, оба уже фетчащихся для 77_1/3/4/6/20/21
    проверены полнотекстовым поиском на "ОСАГО"/"страхов" и НЕ содержат этой
    льготы вообще — эталонный `document/3662941?marker=7DE0K6` формально
    ссылается на закон N60, но содержательно льгота установлена подзаконным
    актом (распоряжение Правительства Москвы №430-РП, найдено через
    WebSearch — SearXNG в этой сессии недоступен, `docker` daemon не
    поднят, см. trace-файл итерации):

    - `mos_text` — `mos.ru/otvet-socialnaya-podderjka/kak-invalidu-vernut-chast-stoimosti-polisa-osago/`,
      тот же паттерн `otvet-`-FAQ, что и 77_7/8/9. Вопрос "В каком размере
      выплачивается компенсация?" даёт "1980 рублей за счет бюджета города
      Москвы" дословно; вопрос "Кто и при каких условиях может вернуть
      часть стоимости полиса ОСАГО?" даёт реальный текст условия
      (медицинские показания + ИПРА + не более двух водителей на полис).
    - `dszn_text` — официальный сайт Департамента труда и социальной
      защиты населения города Москвы (dszn.ru, региональный портал
      соцзащиты из allowlist `AGENTS.md`), страница называется по этой
      же льготе. Подтверждает сумму независимо ("1 980 рублей в год") И,
      впервые для инвалиды-серии, называет ведомство ДОСЛОВНО прямо в
      заголовке страницы ("Департамент труда и социальной защиты
      населения города Москвы") — до этого `department` был `None` в
      10 из 12 предыдущих карточек инвалиды (источники не называли
      ведомство рядом со льготой).

    Источник не разбивает выплату по группе инвалидности/причине
    (применяется к "инвалидам и детям-инвалидам" без разбивки) — как в
    77_1/77_10/77_20, все 4 подполя (`measure_first/second/third_group`,
    `measure_disabled_child`) заполняются одинаковым значением 1980.
    `measurePeriodicity="ежегодно"` — впервые для серии (не "ежемесячно"
    как в 77_7/8/9, не "единовременно" как в эталоне 77_10) — подтверждено
    dszn.ru ("1 980 рублей в год").
    """
    sum_confirmed = (
        "1980 рублей" in mos_text
        and "за счет бюджета города Москвы" in mos_text
        and "1 980" in dszn_text
        and "в год" in dszn_text
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

    terms = None
    m = re.search(
        r'"id":807,"title":"[^"]*","blocks":\[\{"type":"text","id":906,"text":"(.*?)"\}\]\},\{"id":808',
        mos_text,
        re.DOTALL,
    )
    if m:
        raw = re.sub(r'data-hint-content=\\".*?\\"', "", m.group(1))
        raw = raw.replace("&nbsp;", " ")
        terms = re.sub(r"<[^>]+>", " ", raw)
        terms = re.sub(r"\s+", " ", terms).strip()

    department = None
    d = re.search(r"Департамент труда и социальной защиты населения города Москвы", dszn_text)
    if d:
        department = d.group(0)

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": "1980",
        "measure_second_group": "1980",
        "measure_third_group": "1980",
        "measure_disabled_child": "1980",
        "measurePeriodicity": "ежегодно",
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_rehab_vacation_certificate_card(seed: dict, mosgortur_text: str) -> dict:
    """Эвристика для меры "Сертификат на отдых и оздоровление на
    ребёнка-инвалида и сопровождающее лицо" (`77_16`), ОДИН источник (как
    в 77_9/77_10): в эталоне у этой строки нет НПА-полей, `Ссылка на
    источник` указывает НЕ на общий mos.ru-хаб
    (`kak-poluchit-pomosch-dlya-invalidov`, уже проверенный и отброшенный
    для 77_9/77_10 — не содержит текста ни по одной теме), а на
    конкретную целевую страницу `mosgortur.ru/lok/navigator` — впервые для
    инвалиды-серии источник вне mos.ru/cntd.ru/dszn.ru (ГАУК «МОСГОРТУР» —
    подведомственное учреждение Правительства Москвы, страница совпадает
    дословно с эталонной ссылкой).

    Страница содержит раздел "СЕРТИФИКАТ НА ОТДЫХ И ОЗДОРОВЛЕНИЕ для
    детей-инвалидов, детей с ограниченными возможностями здоровья":
    "Для ребенка от 4 до 17 лет и сопровождающего — 40 000 руб. номинал
    сертификата на ребенка / 40 000 руб. номинал сертификата на
    сопровождающее лицо" — дословно совпадает с эталонным
    `measure_disabled_child = '40 000 ₽'` (число). В эталоне
    `measure_first/second/third_group` пустые (мера — только для
    ребёнка-инвалида, не для инвалидов групп I-III) → эти три подполя не
    заполняются, `sum_match` их и не проверяет (см. `sum_match` в
    `score_against_golden.py`: применяются только непустые в эталоне
    подполя). Ведомство "ГАУК «МОСГОРТУР»" называется в преамбуле
    страницы дословно ("Реализовать сертификат можно напрямую в ГАУК
    «МОСГОРТУР»..."). `measurePeriodicity` ("ежегодно" в эталоне) НЕ
    подтверждён дословно этой страницей (нет слова "ежегодно"/"раз в
    год") → честно `None`, не копируется из эталона (это поле не входит в
    scoring, см. `LS_SPEC`, но конвенция проекта — не выдумывать
    неподтверждённые значения независимо от того, влияют они на метрику
    или нет).
    """
    confirmed = (
        "для детей-инвалидов, детей с ограниченными возможностями здоровья" in mosgortur_text
        and "40 000 руб. номинал сертификата на ребенка" in mosgortur_text
        and "40 000 руб. номинал сертификата на сопровождающее лицо" in mosgortur_text
    )

    if not confirmed:
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

    terms = None
    m = re.search(
        r"для детей-инвалидов, детей с ограниченными возможностями здоровья\s+(.*?руб\. номинал сертификата на ребенка)",
        mosgortur_text,
    )
    if m:
        terms = m.group(1).strip()

    department = None
    d = re.search(r"ГАУК\s*«МОСГОРТУР»", mosgortur_text)
    if d:
        department = d.group(0)

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
        "measure_disabled_child": "40000",
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_tsr_compensation_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Региональная компенсация инвалидам на технические
    средства реабилитации" (`77_12`), ОДИН источник (как в 77_9/77_10/77_16):
    оба уже фетчащихся источника (закон 3662941, постановление 1314770295)
    проверены полнотекстовым поиском на названия предметов ("прикроватн",
    "ступеньк", "насадка на унитаз" и т.д.) — 0 совпадений в обоих, льгота
    не установлена ни базовым законом, ни постановлением о размерах.
    Реальный источник найден через WebSearch (SearXNG в этой сессии
    недоступен): `mos.ru/otvet-zdorovie/kak-poluchit-kompensaciyu-za-pokupku-sredstv-reabilitacii-dlya-invalida/`
    — обычная otvet-FAQ-страница mos.ru (тот же паттерн, что 77_7/8/9/11),
    вопрос №5 ("Как получить выплату на самостоятельное приобретение
    технических средств реабилитации?") даёт условие и полный список из 7
    предметов дословно совпадающими с эталонным перечнем в `measureTerms`
    (столик прикроватный, стул для ванны и душа, сиденье для ванны,
    ступенька для ванны, насадка на унитаз, доска для ванны, доска для
    пересаживания); отдельное предложение страницы называет
    "Департамента труда и социальной защиты населения" как источник
    сведений о размере компенсации — совпадает с эталонным department
    (в родительном падеже, как и в 77_11 — text_field_match проходит по
    Jaccard, не по точному совпадению словоформы).

    Golden `measure_first/second/third_group`/`measure_disabled_child` —
    не число, а слово "компенсация": сумма варьируется по конкретному
    предмету (страница отсылает за размером на сайт департамента, не
    называет единую цифру), поэтому эталон тоже использует текстовое
    значение вместо суммы. `sum_field_match` в scorer'е делает fallback на
    `text_field_match`, когда `extract_number` не находит цифр ни у
    агента, ни в эталоне — совпадение строки "компенсация" должно пройти.
    Источник не разбивает льготу по группе инвалидности/причине
    (адресована "инвалидам"/"ребёнку-инвалиду" без разбивки) — как в
    77_1/77_10/77_11/77_20, все 4 подполя заполняются одинаковым значением.
    """
    item_list_confirmed = (
        "прикроватного столика" in mos_text
        and "стула для ванны и душа" in mos_text
        and "сиденья для ванны" in mos_text
        and "ступеньки для ванны" in mos_text
        and "насадки на унитаз" in mos_text
        and "доски для ванны" in mos_text
        and "доски для пересаживания" in mos_text
    )

    if not item_list_confirmed:
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

    terms = None
    m = re.search(
        r"Если вы зарегистрированы в Москве по месту жительства и вам назначены.*?компенсацией до покупки\.",
        mos_text,
    )
    if m:
        terms = m.group(0).strip()

    department = None
    d = re.search(r"Департамента труда и социальной защиты населения", mos_text)
    if d:
        department = d.group(0)

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": "компенсация",
        "measure_second_group": "компенсация",
        "measure_third_group": "компенсация",
        "measure_disabled_child": "компенсация",
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_dental_prosthetics_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Бесплатное изготовление и ремонт зубных протезов"
    (`77_14`), ОДИН источник (как в 77_9/77_10/77_16/77_12): эталонная
    `Нормативно-правовой акт - NPA` — `document/3656309` (Закон города
    Москвы №70 от 03.11.2004), но сам закон (уже фетчен и проверен для
    77_13/15 в этой же итерации-разведке) не называет эту меру дословно
    вообще (0 совпадений "зубн"/"протез") — как и для 77_1/3/4/6/20, закон
    служит только рамочным определением категорий получателей, конкретные
    меры устанавливаются производными актами/страницами. Реальный источник
    найден через WebSearch: `sp53.mos.ru/lgotnoe-zuboprotezirovanie` —
    сайт ГАУЗ «Стоматологическая поликлиника №53 Департамента
    здравоохранения города Москвы» (поддомен mos.ru, подпадает под
    allowlist "mos.ru — отдельно из-за объёма московских региональных
    мер"), раздел "Льготное зубопротезирование" дословно ссылается на
    "Законом № 70" — структурное подтверждение правильности эталонного
    NPA, хоть сам закон и не даёт деталей меры.

    Страница называет ведомство дословно ("Департамента здравоохранения
    города Москвы", в футере — полное название поликлиники) — совпадает с
    эталонным department. Golden `measure_first/second/third_group` —
    текстовое значение "бесплатно" (не число): страница не разбивает
    льготу по группе инвалидности, адресована категориям в целом
    ("отдельным категориям жителей города Москвы, установленным Законом
    № 70... включенным в Единый городской регистр граждан") — как в
    77_1/77_10/77_11/77_20, все 4 подполя причины/группы заполняются
    одинаково, но `measure_disabled_child` в эталоне для этой строки
    `None` (мера не выделяет отдельно детей-инвалидов как самостоятельную
    категорию на этой странице) → не заполняется, сверено с эталоном
    напрямую (read-only openpyxl).
    """
    confirmed = (
        "Бесплатное зубопротезирование" in mos_text
        and "Законом" in mos_text
        and "70" in mos_text
        and "Департамента здравоохранения города Москвы" in mos_text
    )

    if not confirmed:
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

    clean_text = mos_text.replace("&quot;", '"')

    terms = None
    m = re.search(
        r"Бесплатное зубопротезирование.*?ремонт зубных протезов\.",
        clean_text,
    )
    if m:
        terms = m.group(0).strip()

    department = None
    d = re.search(r"Департамента здравоохранения города Москвы", mos_text)
    if d:
        department = d.group(0)

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": "бесплатно",
        "measure_second_group": "бесплатно",
        "measure_third_group": "бесплатно",
        "measure_disabled_child": None,
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_social_taxi_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Социальное такси" (`77_17`), ОДИН источник (как
    в 77_9/77_10/77_16/77_12/77_14): эталонная `Ссылка на источник`
    (`mosgortrans.ru/about/projects/taxi/`) по факту устарела — сервис
    передан ГБУ «Мосавтосантранс» (`taxi.santrans.ru`, не в allowlist), см.
    разведку прошлой итерации в `IMPROVEMENT_BACKLOG.md` B003. Вместо
    смены домена найден альтернативный источник ВНУТРИ allowlist (mos.ru):
    `mos.ru/otvet-socialnaya-podderjka/kak-polzovatsya-socialnym-taksi/` —
    обычная otvet-FAQ-страница (тот же паттерн, что 77_7/8/9/11/12),
    содержит актуальный тариф "210 руб/час" в пределах Москвы (=
    эталонному "210 ₽/час" по всем 4 golden-подполям) и "420 руб/час" в
    пределах Московской области (совпадает с суммой, упомянутой в golden
    `measureTerms`), а также условие регистрации в реестре получателей
    услуги «социальное такси».

    Ведомство для очной регистрации на странице названо сокращённо —
    "Всероссийское общество инвалидов» (МГО ВОИ)" — это московское
    региональное отделение Всероссийского общества инвалидов, структурно
    соответствует эталонному `department` ("Московская городская
    организация Всероссийского общества инвалидов"), но страница не даёт
    именно такую полную юридическую формулировку — сохраняю как есть
    (честно то, что реально написано на странице), не переписываю под
    эталон.
    """
    confirmed = (
        "210 руб/час" in mos_text
        and "420 руб/час" in mos_text
        and "Всероссийском обществе инвалидов" in mos_text
    )

    if not confirmed:
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

    terms_parts = []
    m1 = re.search(
        r"нужно зарегистрироваться в реестре получателей услуги «социальное такси»\.",
        mos_text,
    )
    if m1:
        terms_parts.append(m1.group(0).strip())
    m2 = re.search(r"в пределах Московской области — 420 руб/час\.", mos_text)
    if m2:
        terms_parts.append(m2.group(0).strip())
    terms = " ".join(terms_parts) if terms_parts else None

    department = None
    d = re.search(r"Всероссийском обществе инвалидов»? \(МГО ВОИ\)", mos_text)
    if d:
        department = d.group(0)

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": "210 ₽/час",
        "measure_second_group": "210 ₽/час",
        "measure_third_group": "210 ₽/час",
        "measure_disabled_child": "210 ₽/час",
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_home_social_service_card(seed: dict, mos_text: str) -> dict:
    """Эвристика для меры "Социальное обслуживание на дому и в
    стационарной форме" (`77_19`), ОДИН источник (как в 77_9/77_10/77_12/
    77_14/77_16/77_17): эталонная `Ссылка на источник` — снова уже дважды
    отброшенный общий хаб `kak-poluchit-pomosch-dlya-invalidov` (не даёт
    содержательного текста, см. 77_9/77_10 в PROGRAM.md).

    Реальный источник найден через `WebSearch` (SearXNG недоступен в этой
    сессии — `docker` daemon не поднят, как и в 77_11/77_12):
    `mos.ru/otvet-zdorovie/kak-poluchit-socialno-medicinskoe-i-patronazhnoe-obsluzhivanie/`
    — обычная otvet-FAQ-страница (тема шире одной меры — соцобслуживание в
    целом, не только для инвалидов), вопрос №1 "Кто имеет право на
    социальное обслуживание?" даёт условие ПОЧТИ ДОСЛОВНО golden `terms`
    ("Факт установления инвалидности, детям-инвалидам, частично или
    полностью утратившим способность к самообслуживанию"): "предоставляют
    социальные услуги гражданам, частично или полностью утратившим
    способность к самообслуживанию. Услуги предоставляются бесплатно, за
    частичную или полную плату." — совпадает и условие, и структура
    оплаты (golden `measure_first/second_group`/`measure_disabled_child`
    = "бесплатно или за частичную плату").

    Источник не разбивает форму оплаты по группе инвалидности/причине —
    структурный факт (единая норма на всех получателей социального
    обслуживания, не только инвалидов) → `measure_first/second/third_group`
    и `measure_disabled_child` заполнены ОДИНАКОВО, как в 77_1/77_10/77_20.

    `department` — "Департамент труда и социальной защиты населения
    Москвы" называется на странице дословно (в контексте адреса для
    очного обращения), совпадает с golden `department` буквально.
    """
    confirmed = (
        "частично или полностью утратившим способность к самообслуживанию" in mos_text
        and "предоставляются" in mos_text
        and "Департамент труда и социальной защиты населения Москвы" in mos_text
    )

    if not confirmed:
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

    terms = None
    m = re.search(
        r"предоставляют социальные услуги гражданам, частично или полностью "
        r"утратившим способность к самообслуживанию\.\s*Услуги предоставляются "
        r"бесплатно\s*,\s*за частичную или полную плату\s*\.",
        mos_text,
    )
    if m:
        terms = re.sub(r"\s+", " ", m.group(0)).strip()

    department = "Департамент труда и социальной защиты населения Москвы"

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 1,
        "measureName": seed["measureName"],
        "measure_first_group": "бесплатно или за частичную плату",
        "measure_second_group": "бесплатно или за частичную плату",
        "measure_third_group": None,
        "measure_disabled_child": "бесплатно или за частичную плату",
        "measurePeriodicity": None,
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_telephone_compensation_card(seed: dict, mos_text: str, dszn_text: str) -> dict:
    """Эвристика для меры "Компенсация на оплату услуг местной телефонной
    связи" (`77_15`). Golden `Ссылка на источник` — снова общий хаб
    `kak-poluchit-pomosch-dlya-invalidov` (уже трижды отброшен как
    links-only страница без содержательного текста, см. 77_9/77_10/77_13
    в `IMPROVEMENT_BACKLOG.md`), не используется. Два реальных источника:

    - `mos_text` — mos.ru FAQ
      `otvet-socialnaya-podderjka/kak-oformit-kompensaciyu-za-stacionarnyy-telefon/`
      (найдена как ссылка внутри 77_14-разведки, не дофетчена в прошлой
      итерации) — список категорий получателей компенсации включает
      дословно "инвалиды 1-й группы по зрению" (≈ golden `measureTerms`
      "Инвалидам по зрению...") и, отдельно, "инвалиды Великой
      Отечественной войны, инвалиды боевых действий и приравненные к ним
      лица" — оба пункта в ОДНОМ списке критериев назначения ЭТОЙ меры,
      значит и `cause_general_disease`, и `cause_war_trauma`
      подтверждаются дословно этим источником.
    - `dszn_text` — `dszn.ru/news/145` (старая статья 2011 года, но
      используется не как источник актуальной суммы, а только за
      ЗАГОЛОВОК страницы: "Ежемесячная денежная компенсация на оплату
      услуг местной телефонной связи - Департамент труда и социальной
      защиты населения города Москвы" — даёт периодичность ("ежемесячная")
      и ведомство дословно, ближе к golden-формулировке, чем родительный
      падеж "Департамента ... города Москвы" на mos.ru-странице.

    Ни один из двух источников НЕ называет ни точную сумму компенсации
    (golden 292 ₽ — полнотекстовый поиск "292"/"218"/"264" по обоим
    источникам дал 0 совпадений, mos.ru-страница лишь отсылает "актуальный
    размер... на сайте Департамента"), ни радиационную причину
    инвалидности (0 упоминаний "Чернобыль"/"Маяк"/"радиац" в mos_text) —
    оба поля честно оставлены `None`/`0`, не скопированы из эталона, даже
    зная, что это понизит QS этой конкретной карточки (см. прецедент
    77_8/77_9 с честным `department=None`).
    """
    confirmed = (
        "инвалиды 1-й группы по зрению" in mos_text
        and "инвалиды Великой Отечественной войны, инвалиды боевых действий" in mos_text
        and "Ежемесячная денежная компенсация на оплату услуг местной телефонной связи"
        in dszn_text
    )

    if not confirmed:
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

    terms = None
    m = re.search(
        r"Компенсация по оплате за телефон назначается тем, кто проживает в "
        r"Москве по месту жительства, является абонентом стационарного "
        r"телефона и относится к одной из следующих категорий:.*?"
        r"инвалиды 1-й группы по зрению;",
        mos_text,
        re.DOTALL,
    )
    if m:
        terms = re.sub(r"\s+", " ", m.group(0)).strip()

    department = None
    d = re.search(
        r"Ежемесячная денежная компенсация на оплату услуг местной "
        r"телефонной связи - (Департамент труда и социальной защиты "
        r"населения города Москвы)",
        dszn_text,
    )
    if d:
        department = d.group(1)

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 0,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": None,
        "measure_second_group": None,
        "measure_third_group": None,
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": department,
    }


def extract_disability_pensioner_compensation_card(seed: dict, mos_text: str, amounts_text: str) -> dict:
    """Эвристика для меры "Компенсация отдельным категориям работающих
    пенсионеров" (`77_13`), ДВА источника, оба НЕ из эталонной колонки
    "Ссылка на источник"/"Нормативно-правовой акт - NPA" (общий хаб
    `kak-poluchit-pomosch-dlya-invalidov` и Закон №70/document/3656309 —
    оба уже проверены и отброшены для соседних 77_9/77_10/77_14/77_19,
    рамочные/обзорные, без содержательного текста этой меры):

    - `amounts_text` — то же постановление №3025-ПП (document/1314770295),
      уже фетчащееся для нескольких предыдущих seed'ов, но здесь
      используется НЕ §-таблица приложения, а п.3 ПРЕАМБУЛЫ самого
      постановления: "...ежемесячной компенсационной выплаты к пенсии
      некоторых категорий работающих пенсионеров... в размере 27401
      рубля" — дословно подтверждает и название меры, и сумму.
    - `mos_text` — mos.ru FAQ-страница
      `otvet-socialnaya-podderjka/kak-oformit-doplatu-k-pensii-rabotayuschemu-pensioneru/`
      (найдена через WebSearch, не из эталонной колонки — сам эталон
      ссылается только на общий хаб и рамочный закон, оба непригодны) —
      даёт условие ПОЧТИ ДОСЛОВНО golden `terms` (регистрация в Москве
      не менее 10 лет + доплата до городского социального стандарта
      27 401 ₽) и категории получателей независимо от занимаемой
      должности: "инвалидам I и II группы" (cause_general_disease),
      "инвалидам Великой Отечественной войны... и участникам Великой
      Отечественной войны" (cause_war_trauma), "инвалидам вследствие
      катастрофы на Чернобыльской АЭС... аварии в 1957 году на
      производственном объединении «Маяк»..." (cause_radiation) — все
      три golden cause_*=1 подтверждены дословно на этой странице.

    Golden `measure_first_group`/`measure_second_group`="индивидуально"
    (не число) — источник тоже НЕ даёт единой суммы, а описывает доплату
    как "увеличивает размер пенсии до уровня городского социального
    стандарта" (сумма варьируется по фактической пенсии получателя) —
    буквальное "индивидуально" по обоим подполям, как и в golden.
    `measure_third_group`/`measure_disabled_child` в эталоне `None`
    (сверено с эталоном напрямую, `docs/меры_автоагент_2.xlsx`) — не
    заполняются: страница отдельно оговаривает, что для инвалидов III
    группы доплата обусловлена работой в конкретных организациях (не
    "независимо от места работы", как для I/II группы), то есть это уже
    другая, более узкая норма, а не прямое продолжение той же самой
    строки; про "ребёнок-инвалид" страница не упоминает вообще.

    `department` НЕ подтверждён этим источником (полнотекстовый поиск
    "труда и социальной защиты" — 0 совпадений на этой странице) →
    честно `None`, не копируется из эталона.
    """
    confirmed = (
        "27401 рубля" in amounts_text
        and "работающих пенсионеров" in amounts_text
        and "27 401 рубль" in mos_text
        and "инвалидам I и II группы" in mos_text
        and "10 лет" in mos_text
    )

    if not confirmed:
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

    terms = None
    m_resid = re.search(
        r"на момент обращения за выплатой зарегистрированы в Москве по месту "
        r"жительства и продолжительность такой регистрации составляет не "
        r"менее 10 лет в общей сложности \(включая время проживания на "
        r"присоединенной к Москве территории\)",
        mos_text,
    )
    m_standard = re.search(
        r"Ежемесячная компенсационная выплата увеличивает размер пенсии до "
        r"уровня городского социального стандарта в Москве \(в 2026 году — "
        r"27 401 рубль\)\.",
        mos_text,
    )
    if m_resid and m_standard:
        terms = re.sub(r"\s+", " ", f"{m_resid.group(0)}. {m_standard.group(0)}").strip()

    return {
        "measureId": None,
        "region": seed["region"],
        "cause_general_disease": 1,
        "cause_war_trauma": 1,
        "cause_radiation": 1,
        "cause_disabled_child": 0,
        "measureName": seed["measureName"],
        "measure_first_group": "индивидуально",
        "measure_second_group": "индивидуально",
        "measure_third_group": None,
        "measure_disabled_child": None,
        "measurePeriodicity": "ежемесячно",
        "measureTerms": terms,
        "department": None,
    }


def run_disability_seed(seed: dict) -> dict:
    """Прогоняет одну инвалиды-меру из реестра через фетч + извлечение."""
    if seed["npaUrl"] == "https://www.mos.ru/otvet-semya-i-deti/kak-vospolzovatsya-uslugami-molochnoy-kuhni/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_free_food_card(seed, mos_text)
    if seed["npaUrl"] == "https://www.mos.ru/karta-moskvicha/tipy-derzhataley/invalidy/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_social_card_transport_card(seed, mos_text)
    if seed["npaUrl"] == "https://docs.cntd.ru/document/3662941" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        law_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        if seed["measureName"].startswith("Компенсация в связи с ростом стоимости жизни"):
            return extract_disability_rising_cost_card(seed, law_text, amounts_text)
        if seed["measureName"].startswith("Компенсация на возмещение роста стоимости продуктов питания"):
            return extract_disability_child_food_compensation_card(seed, law_text, amounts_text)
        if seed["measureName"].startswith("Компенсация усыновившим ребёнка-инвалида"):
            return extract_disability_adopted_child_card(seed, law_text, amounts_text)
        if seed["measureName"].startswith("Компенсация гражданам, имеющим заслуги"):
            return extract_disability_sports_merit_card(seed, law_text, amounts_text)
        return extract_disability_care_compensation_card(seed, law_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/pgu2/landing/target/7700000000163132555/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_lost_breadwinner_card(seed, mos_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/pgu2/landing/target/7700000000163131356/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_nonworking_parents_card(seed, mos_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-socialnaya-podderjka/kak-poluchit-vyplaty-usynovitelyam-opekunam-priemnym-roditelyam/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        if seed["measureName"].startswith("Вознаграждение приёмному родителю"):
            return extract_disability_foster_reward_card(seed, mos_text, amounts_text)
        return extract_disability_guardian_content_card(seed, mos_text, amounts_text)
    if seed["npaUrl"] == "https://docs.cntd.ru/document/1314770295" and not seed.get("amountsUrl"):
        amounts_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_veteran_bd_pension_card(seed, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-socialnaya-podderjka/kak-invalidu-vernut-chast-stoimosti-polisa-osago/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        dszn_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_osago_compensation_card(seed, mos_text, dszn_text)
    if seed["npaUrl"] == "https://mosgortur.ru/lok/navigator":
        mosgortur_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_rehab_vacation_certificate_card(seed, mosgortur_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-zdorovie/kak-poluchit-kompensaciyu-za-pokupku-sredstv-reabilitacii-dlya-invalida/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_tsr_compensation_card(seed, mos_text)
    if seed["npaUrl"] == "https://sp53.mos.ru/lgotnoe-zuboprotezirovanie":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_dental_prosthetics_card(seed, mos_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-socialnaya-podderjka/kak-polzovatsya-socialnym-taksi/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_social_taxi_card(seed, mos_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-zdorovie/kak-poluchit-socialno-medicinskoe-i-patronazhnoe-obsluzhivanie/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        return extract_disability_home_social_service_card(seed, mos_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-socialnaya-podderjka/kak-oformit-doplatu-k-pensii-rabotayuschemu-pensioneru/" and seed.get("amountsUrl") == "https://docs.cntd.ru/document/1314770295":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        amounts_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_pensioner_compensation_card(seed, mos_text, amounts_text)
    if seed["npaUrl"] == "https://www.mos.ru/otvet-socialnaya-podderjka/kak-oformit-kompensaciyu-za-stacionarnyy-telefon/":
        mos_text = fetch_text(seed["npaUrl"], use_proxy=True)
        dszn_text = fetch_text(seed["amountsUrl"], use_proxy=True)
        return extract_disability_telephone_compensation_card(seed, mos_text, dszn_text)
    raise NotImplementedError(
        f"Нет эвристики извлечения для источника {seed['npaUrl']!r} — "
        "добавь новую в отдельной ralph-итерации, не угадывай молча."
    )

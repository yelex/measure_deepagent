"""Генерический LLM-экстрактор карточки меры — замена per-измерения
regex-эвристик в `pipeline.py` (см. `IMPROVEMENT_BACKLOG.md`, задача про
переход на реальный B001).

Одна функция `extract_via_llm(seed, texts, ls)` вместо десятков
`extract_<measure>_card`: модели передаётся текст источника(ов) + схема
полей нужной ЖС, модель обязана вернуть для каждого поля значение **и
дословную цитату**, на которой оно основано. Код после ответа проверяет
цитату по тексту (`_quote_confirmed`) — поле без реально проверяемой
цитаты обнуляется, а не остаётся "на веру" (тот же принцип "не
придумывать", что и в regex-эвристиках, см. AGENTS.md).

Провайдеры — GLM (api.z.ai, OpenAI-совместимый `/chat/completions`) и
GigaChat (Sber, отдельный OAuth2-обмен на токен). Оба российских
эндпоинта используют сертификаты, не входящие в системный доверенный
список (тот же класс проблемы, что и `mos.ru` — см. `pipeline.py`
docstring про ГОСТ-TLS) — проверка отключена точечно только для этих
двух хостов, по тому же прецеденту, что и в `auto/eval/llm_factory.py`
(`verify=False`).

Ключи читаются из окружения (`.env`, не в git):
`GLM_API_KEY`/`GLM_BASE_URL`, `GIGACHAT_CREDENTIALS`/`GIGACHAT_AUTH_URL`/
`GIGACHAT_BASE_URL`/`GIGACHAT_SCOPE`.

Экстракция кэшируется вызывающей стороной (не здесь) — см.
`data/output/llm_extraction_cache.json` и `run_seed_cached` в
`scripts/run_pipeline_demo.py`, чтобы обычные eval-прогоны не били по
платному API на каждой карточке.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

# --- Схема полей по ЖС (дублирует models.LS_FIELDS + описания для промпта) --

FIELD_SCHEMA = {
    "вбд": {
        "measureName": "официальное/принятое название меры (текст)",
        "categoryOfVeteran": "категория получателя (текст, например 'Военнослужащие', 'Обслуживающие воинские части')",
        "measureSum": "размер меры (число или текстовое описание льготы)",
        "measureTerms": "условия предоставления (кому положено, при каких условиях)",
        "department": "куда обращаться / кто оформляет (ведомство, организация)",
    },
    "сво": {
        "measureName": "официальное/принятое название меры (текст)",
        "categoryMobilized": "применяется ли к мобилизованным (0 или 1)",
        "categoryContractor": "применяется ли к заключившим контракт (0 или 1)",
        "categoryVolunteer": "применяется ли к добровольцам (0 или 1)",
        "kidsOfMilitary": "применяется ли к детям участников СВО (0 или 1)",
        "measureSum": "размер меры (число или текстовое описание льготы)",
        "measureTerms": "условия предоставления",
        "department": "куда обращаться / кто оформляет",
    },
    "инвалиды": {
        "measureName": "официальное/принятое название меры (текст)",
        "cause_general_disease": "применяется при инвалидности по общему заболеванию (0 или 1)",
        "cause_war_trauma": "применяется при инвалидности вследствие военной травмы (0 или 1)",
        "cause_radiation": "применяется при инвалидности вследствие радиации (0 или 1)",
        "cause_disabled_child": "применяется к ребёнку-инвалиду (0 или 1)",
        "measure_first_group": "размер для 1 группы инвалидности (число или текст)",
        "measure_second_group": "размер для 2 группы инвалидности (число или текст)",
        "measure_third_group": "размер для 3 группы инвалидности (число или текст)",
        "measure_disabled_child": "размер для ребёнка-инвалида (число или текст)",
        "measureTerms": "условия предоставления",
        "department": "куда обращаться / кто оформляет",
    },
}


# --- Провайдеры -------------------------------------------------------------

_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE


def _call_glm(system_prompt: str, user_prompt: str, max_tokens: int = 8000, retries: int = 2) -> str:
    api_key = os.environ["GLM_API_KEY"]
    base_url = os.environ["GLM_BASE_URL"]
    body = json.dumps({
        "model": os.environ.get("GLM_MODEL", "glm-5"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode("utf-8")
    req_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(base_url + "/chat/completions", data=body, method="POST", headers=req_headers)
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if content.strip():
                return content
            last_err = RuntimeError(
                f"GLM вернул пустой content (finish_reason={data['choices'][0].get('finish_reason')!r}) — "
                "вероятно max_tokens исчерпан на reasoning_content"
            )
        except urllib.error.HTTPError as e:
            last_err = e
        if attempt < retries:
            time.sleep(3)
    raise last_err


def _gigachat_token() -> str:
    creds = os.environ["GIGACHAT_CREDENTIALS"]
    auth_url = os.environ["GIGACHAT_AUTH_URL"]
    scope = os.environ["GIGACHAT_SCOPE"]
    body = f"scope={scope}".encode("utf-8")
    req = urllib.request.Request(
        auth_url, data=body, method="POST",
        headers={"Authorization": f"Basic {creds}", "RqUID": str(uuid.uuid4()),
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30, context=_NO_VERIFY_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def _call_gigachat(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> str:
    base_url = os.environ["GIGACHAT_BASE_URL"]
    token = _gigachat_token()
    body = json.dumps({
        "model": "GigaChat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90, context=_NO_VERIFY_CTX) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


PROVIDERS = {
    "glm": _call_glm,
    "gigachat": _call_gigachat,
}


# --- Промпт + парсинг + проверка цитат --------------------------------------

SYSTEM_PROMPT = """Извлеки данные о мере соцподдержки из текста НПА.

ПРАВИЛА:
1. Только из текста источника. Не придумывай.
2. Для каждого поля: value (значение) + quote (дословная цитата).
3. Нет в тексте → value: null, quote: null.
4. 0/1 поля: 1 если текст подтверждает, 0 если явно исключает, null если не упоминается.
5. ГРУППЫ полей (категории получателей, группы инвалидности): если текст даёт одно условие на всю группу, не разбивая — верни одно и то же value для всех полей группы.
6. department — краткое имя ведомства (не полное юридическое название).
7. measureSum — только число (цифры), или null если мера не денежная.
8. Ответ: ТОЛЬКО JSON, без markdown. Кавычки внутри строк — «ёлочки».

ВАЖНО: категории получателей (мобилизованные, контрактники, добровольцы, дети) обычно указаны в ПРЕАМБУЛЕ документа или в первом абзаце — обязательно проверь начало текста."""


# Группы полей на одну ось — см. правило 5 в SYSTEM_PROMPT: если текст не
# разбивает значение по осям группы, одно и то же value/quote идёт во ВСЕ
# поля группы, а не только в одно.
FIELD_GROUPS = {
    "вбд": [],
    "сво": [["categoryMobilized", "categoryContractor", "categoryVolunteer", "kidsOfMilitary"]],
    "инвалиды": [
        ["cause_general_disease", "cause_war_trauma", "cause_radiation", "cause_disabled_child"],
        ["measure_first_group", "measure_second_group", "measure_third_group", "measure_disabled_child"],
    ],
}


def _strip_boilerplate(text: str) -> str:
    """Убирает навигационный мусор r.jina.ai/cntd.ru перед текстом НПА.

    jina возвращает: [Title, URL Source, Published Time, навигация cntd.ru,
    хлебные крошки, кнопки Войти/Зарегистрироваться...] и только потом
    реальный текст указа. Этот мусор занимает ~1500-2000 символов и
    съедает весь бюджет преамбулы в _cut_to_relevant.
    """
    # Убираем markdown-ссылки [текст](url) → текст
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Убираем голые URL
    text = re.sub(r"https?://\S+", "", text)
    # Убираем пунктирные разделители
    text = re.sub(r"_{5,}", "", text)
    # Сжимаем пробелы/переносы
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    # Ищем маркеры начала реального текста НПА
    markers = [
        "УКАЗ", "Указ", "ФЕДЕРАЛЬНЫЙ ЗАКОН", "Федеральный закон",
        "ЗАКОН", "Закон", "ПОСТАНОВЛЕНИЕ", "Постановление",
        "РАСПОРЯЖЕНИЕ", "Распоряжение",
    ]
    best_pos = len(text)
    for marker in markers:
        pos = text.find(marker)
        if 100 <= pos < best_pos:
            best_pos = pos
    if best_pos < len(text):
        return text[best_pos:]
    return text


def _cut_to_relevant(text: str, measure_name: str, window: int = 6000) -> str:
    """Обрезает текст до релевантного окна вокруг названия меры + преамбулы.

    Проблема: полный текст указа (27K симв.) модель читает долго и
    пропускает категории в преамбуле. Решение: дать первые 2500 символов
    от реального начала текста НПА (преамбула + категории получателей)
    + окно вокруг упоминания меры.
    """
    text = _strip_boilerplate(text).strip()
    if len(text) <= window:
        return text

    # Первые 2500 символов от чистого текста — преамбула с категориями
    preamble = text[:2500]

    # Найти позицию ближе к названию меры
    keywords = measure_name.split()[:3]
    best_pos = -1
    for kw in keywords:
        pos = text.lower().find(kw.lower())
        if pos > 2500:  # не в преамбуле, там уже есть
            best_pos = max(best_pos, pos)

    if best_pos == -1:
        # Не нашли — даём преамбулу + следующий кусок
        remaining = text[2500:2500 + window - 2500]
    else:
        # Окно ±1500 вокруг найденной позиции
        start = max(2500, best_pos - 1500)
        end = min(len(text), start + window - 2500)
        remaining = text[start:end]
        if start > 2500:
            remaining = "[...пропуск...] " + remaining
        if end < len(text):
            remaining = remaining + " [...пропуск...]"

    return preamble + "\n\n" + remaining


def _build_user_prompt(seed: dict, texts: dict, ls: str) -> str:
    schema = FIELD_SCHEMA[ls]
    fields_desc = "\n".join(f'- "{k}": {v}' for k, v in schema.items())
    sources = "\n\n".join(f"=== Источник «{name}» ===\n{_cut_to_relevant(text, seed['measureName'])}" for name, text in texts.items())
    fields_json_example = ", ".join(f'"{k}": {{"value": ..., "quote": ...}}' for k in schema)

    groups_note = ""
    groups = FIELD_GROUPS.get(ls) or []
    if groups:
        groups_desc = "\n".join(f"- {{{', '.join(g)}}}" for g in groups)
        groups_note = f"""

Для ЭТОЙ ЖС группы полей на одну ось (см. правило 5):
{groups_desc}
Проверь явно для каждой группы: текст разбивает значение по подполям
группы, или даёт одно значение на всю группу? Если одно — примени правило
5 буквально ко ВСЕМ полям группы, не только к тем, что упомянуты дословно
ближе всего к найденной цитате."""

    return f"""Мера: "{seed['measureName']}" (регион: {seed['region']}).

Поля для извлечения:
{fields_desc}
{groups_note}

{sources}

Верни JSON вида: {{{fields_json_example}}}"""


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _quote_confirmed(quote, texts: dict) -> bool:
    if not quote:
        return False
    q = _normalize_ws(str(quote))
    if not q:
        return False
    return any(q in _normalize_ws(t) for t in texts.values())


def _escape_stray_inner_quotes(s: str) -> str:
    """Чинит частую поломку JSON от LLM: `"`-символ внутри значения
    строки (например дословная русская цитата с "кавычками" вместо
    «ёлочек»), из-за которого parser видит преждевременный конец
    строки. Проходит по символам, отслеживая, находимся ли мы внутри
    JSON-строки; `"` внутри строки, за которым (без пробелов) НЕ следует
    структурный символ `:,}]` — это, скорее всего, незаэкранированная
    внутренняя кавычка, а не конец значения; она экранируется."""
    out = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                j = i + 1
                while j < n and s[j] in " \t\n\r":
                    j += 1
                if j >= n or s[j] in ":,}]":
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
    return "".join(out)


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    # некоторые модели всё равно оборачивают в ```json ... ``` вопреки промпту
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"Ответ модели не содержит JSON-объекта: {raw[:300]!r}")
    blob = m.group(0)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    # повторная попытка после эвристического ремонта незаэкранированных кавычек
    repaired = _escape_stray_inner_quotes(blob)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Ответ модели не парсится как JSON даже после ремонта кавычек: {e}. "
            f"Первые 500 симв.: {blob[:500]!r}"
        )


def extract_via_llm(seed: dict, texts: dict, ls: str, provider: str = "glm") -> dict:
    """texts: {"название источника": "текст"} — один или несколько источников.
    Возвращает карточку в каноническом формате (см. models.LS_FIELDS), с
    None на любом поле, чью цитату не удалось подтвердить в texts."""
    call = PROVIDERS[provider]
    user_prompt = _build_user_prompt(seed, texts, ls)
    raw = call(SYSTEM_PROMPT, user_prompt)
    parsed = _parse_json_response(raw)

    # Важно: проверяем цитаты против ОЧИЩЕННОГО текста, который модель
    # реально видела (после _strip_boilerplate + _cut_to_relevant), а не
    # против сырого texts — иначе URL-мусор и обрезка ломают матчинг.
    clean_texts = {
        name: _cut_to_relevant(_strip_boilerplate(text), seed["measureName"])
        for name, text in texts.items()
    }

    card = {"measureId": None, "region": seed["region"], "measureName": seed["measureName"]}
    for field_name in FIELD_SCHEMA[ls]:
        if field_name == "measureName":
            continue
        entry = parsed.get(field_name) or {}
        value = entry.get("value") if isinstance(entry, dict) else None
        quote = entry.get("quote") if isinstance(entry, dict) else None
        card[field_name] = value if _quote_confirmed(quote, clean_texts) else None

    card.setdefault("measurePeriodicity", None)
    return card

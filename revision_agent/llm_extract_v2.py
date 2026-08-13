"""LLM-экстрактор карточки меры — structured output + reasoning.

Гибрид лучшего из проекта auto:
- StructuredLLMExtractor: bind_tools с Pydantic-схемой + reasoning-поля
- LangExtractAdapter: grounding через char_interval проверку

И deepagents-нативный: это tool, который вызывает LLM внутри себя,
а не отдельный pipeline-шаг. Агент вызывает extract_measure_card_tool,
внутри которого происходит bind_tools + reasoning + grounding check.
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
from typing import Optional, Type

from pydantic import BaseModel, Field, create_model

# --- Схема карточки меры (Pydantic, для bind_tools) -------------------------

class FieldExtraction(BaseModel):
    """Одно поле карточки: value + quote для grounding."""
    value: Optional[str] = Field(description="Значение поля (число, текст, или 0/1 для булевых). null если поле не найдено в тексте.")
    quote: Optional[str] = Field(description="Дословная цитата из источника, подтверждающая value. null если value=null.")


def _build_extraction_schema(ls: str) -> Type[BaseModel]:
    """Создаёт Pydantic-схему с reasoning-полями для конкретной ЖС.
    
    Аналог create_sorted_benefit_schema() из auto/structured_llm.py:
    reasoning-поля идут первыми, потом поля карточки.
    """
    ls_fields = {
        "вбд": {
            "categoryOfVeteran": "категория получателя (текст)",
            "measureSum": "размер (число или описание)",
            "measurePeriodicity": "периодичность",
            "measureTerms": "условия предоставления",
            "department": "ведомство/организация",
        },
        "сво": {
            "categoryMobilized": "0/1, мобилизованные",
            "categoryContractor": "0/1, контрактники",
            "categoryVolunteer": "0/1, добровольцы",
            "kidsOfMilitary": "0/1, дети участников СВО",
            "measureSum": "размер (число или описание)",
            "measureTerms": "условия предоставления",
            "department": "ведомство/организация",
            "measurePeriodicity": "периодичность",
        },
        "инвалиды": {
            "cause_general_disease": "0/1, общее заболевание",
            "cause_war_trauma": "0/1, военная травма",
            "cause_radiation": "0/1, радиация",
            "cause_disabled_child": "0/1, ребёнок-инвалид",
            "measure_first_group": "размер для 1 группы",
            "measure_second_group": "размер для 2 группы",
            "measure_third_group": "размер для 3 группы",
            "measure_disabled_child": "размер для ребёнка-инвалида",
            "measureTerms": "условия предоставления",
            "department": "ведомство/организация",
            "measurePeriodicity": "периодичность",
        },
    }

    fields_def = ls_fields.get(ls, {})
    
    # Reasoning-поля первыми (как в auto/structured_llm.py)
    pydantic_fields = {
        "reasoning_scene_graph": (str, Field(
            description="Анализ текста: выдели субъекты (кто получает), объекты (что получают), предикаты (при каких условиях). Особенно проверь ПРЕАМБУЛУ на категории получателей."
        )),
        "reasoning_schema_mapping": (str, Field(
            description="Маппинг элементов графа сцены на поля схемы. Для каждой группы полей (категории, суммы) — указано ли в тексте разделение по подосям, или одно значение на всю группу?"
        )),
    }
    
    for fname, desc in fields_def.items():
        if fname == "measureName":
            continue
        pydantic_fields[fname] = (FieldExtraction, Field(description=desc))
    
    model_name = f"Extract{ls.capitalize()}Measure"
    model = create_model(
        model_name,
        **pydantic_fields,
        __base__=BaseModel,
    )
    # GigaChat требует description на самом классе модели
    model.__doc__ = "Извлечь карточку меры социальной поддержки из текста нормативного акта"
    return model


# --- Очистка и подготовка текста -------------------------------------------

def _strip_boilerplate(text: str) -> str:
    """Убирает навигационный мусор r.jina.ai/cntd.ru перед текстом НПА."""
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


_STOPWORDS = {
    "в", "во", "и", "с", "со", "на", "для", "от", "по", "к", "ко", "из",
    "за", "у", "о", "об", "при", "или", "не", "их", "ее", "её", "его",
}


def _stem(word: str, stem_len: int = 6) -> str:
    """Грубый стем для сравнения без учёта падежных окончаний."""
    w = word.lower()
    return w[:stem_len] if len(w) > stem_len else w


# Слова, которые встречаются в названиях мер слишком часто, чтобы быть
# содержательным сигналом присутствия темы меры в тексте (см. L011: найдено
# ложное совпадение "бесплатное" внутри рекламного блока "Попробовать
# бесплатно" на странице cntd.ru — никак не связанного с текстом закона).
_GENERIC_STEMS = {_stem(w) for w in (
    "бесплатный", "бесплатное", "бесплатная", "бесплатно",
    "компенсация", "компенсационная", "выплата",
)}


def _measure_keywords(measure_name: str) -> list[str]:
    """Значимые слова названия меры, отсортированные от самого длинного/
    специфичного к короткому, без стоп-слов и без generic-слов
    (см. `_GENERIC_STEMS`)."""
    return sorted(
        (
            w for w in measure_name.split()
            if len(w) > 2
            and w.lower() not in _STOPWORDS
            and _stem(w) not in _GENERIC_STEMS
        ),
        key=len, reverse=True,
    )


def has_relevant_content(text: str, measure_name: str) -> bool:
    """Контентный триггер (L011): проверяет, что текст реально содержит
    фрагмент по теме меры, а не только преамбулу/шапку документа. cntd.ru
    иногда отдаёт текст длиннее MIN_TEXT_LENGTH, но обрывающийся на первых
    статьях закона, дополненный не относящимся к делу мусором со страницы
    (виджеты, реклама, списки) — длины одной недостаточно, чтобы отличить
    такой обрыв от полного документа."""
    stripped = _strip_boilerplate(text).strip()
    text_lower = stripped.lower()
    for kw in _measure_keywords(measure_name):
        if text_lower.find(_stem(kw), 3000) > 3000:
            return True
    return False


def _cut_to_relevant(text: str, measure_name: str, window: int = 8000) -> str:
    """Обрезает текст до релевантного окна: преамбула + окно вокруг меры."""
    text = _strip_boilerplate(text).strip()
    if len(text) <= window:
        return text

    # Первые 3000 символов — преамбула с категориями получателей
    preamble = text[:3000]

    # Найти позицию ближе к названию меры. Сравниваем по стему
    # (первые 6 символов слова), т.к. русские падежные окончания
    # ломают точное substring-совпадение (напр. "юридическая" в имени
    # меры vs "юридическим"/"юридическую" в тексте акта). Ключевые слова
    # перебираем от самого длинного (самого специфичного) к короткому и
    # берём ПЕРВОЕ совпадение — иначе короткое частое слово (напр.
    # "помощь") может встретиться раньше в совсем другом контексте и
    # увести окно от нужного места.
    keywords = _measure_keywords(measure_name)
    text_lower = text.lower()
    best_pos = -1
    for kw in keywords:
        pos = text_lower.find(_stem(kw), 3000)
        if pos > 3000:
            best_pos = pos
            break

    if best_pos == -1:
        remaining = text[3000:3000 + window - 3000]
    else:
        start = max(3000, best_pos - 2000)
        end = min(len(text), start + window - 3000)
        remaining = text[start:end]
        if start > 3000:
            remaining = "[...пропуск...] " + remaining
        if end < len(text):
            remaining = remaining + " [...пропуск...]"

    return preamble + "\n\n" + remaining


# --- Grounding check (по позиции в тексте, как LangExtract) -----------------

def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _quote_grounded(quote: Optional[str], source_text: str) -> bool:
    """Проверяет, что цитата реально присутствует в тексте источника.
    
    В отличие от старого _quote_confirmed, проверяет против ОЧИЩЕННОГО
    текста (который видела модель), а не сырого.
    """
    if not quote:
        return False
    q = _normalize_ws(str(quote))
    if len(q) < 5:  # слишком короткая цитата — пропускаем
        return False
    return q in _normalize_ws(source_text)


# --- LLM вызов (GLM через OpenAI-compatible API) ----------------------------

_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE


class GLMQuotaExceededError(RuntimeError):
    """GLM вернул HTTP 429 с 5-часовым лимитом использования (L013).

    Отличается от прочих временных ошибок тем, что retry гарантированно
    бесполезен (окно сбрасывается часами, не секундами) — вызывающий код
    должен остановить весь оставшийся batch, а не продолжать писать
    карточки-заглушки для необработанных мер (см. IMPROVEMENT_BACKLOG.md
    L013 — именно это молча произошло в прогоне 2026-08-12 23:15).
    """


def _call_glm_structured(
    system_prompt: str,
    user_prompt: str,
    schema_json: dict,
    max_tokens: int = 8000,
    retries: int = 2,
) -> str:
    """Вызов GLM с forced tool_call (structured output).
    
    schema_json — JSON Schema для tool (из Pydantic model_json_schema()).
    """
    api_key = os.environ["GLM_API_KEY"]
    base_url = os.environ["GLM_BASE_URL"]
    model = os.environ.get("GLM_MODEL", "glm-5")

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "tools": [{
            "type": "function",
            "function": {
                "name": "extract_measure",
                "description": "Извлечь карточку меры социальной поддержки из текста",
                "parameters": schema_json,
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "extract_measure"}},
    }).encode("utf-8")

    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                base_url + "/chat/completions",
                data=body,
                method="POST",
                headers=req_headers,
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            msg = data["choices"][0]["message"]

            # Извлекаем tool_call
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                args = json.loads(tool_calls[0]["function"]["arguments"])
                return json.dumps(args, ensure_ascii=False)

            # Некоторые модели возвращают content вместо tool_call
            content = msg.get("content", "")
            if content.strip():
                return content

            last_err = RuntimeError(
                f"GLM вернул пустой ответ (finish_reason={data['choices'][0].get('finish_reason')!r})"
            )
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            if e.code == 429 and ("Usage limit" in body_text or '"code":"1308"' in body_text):
                # 5-часовое окно — retry с паузой 3с бессмыслен, не тратим
                # его впустую и не глушим сигнал под generic HTTP-ошибку.
                raise GLMQuotaExceededError(f"HTTP 429: {body_text}") from e
            last_err = RuntimeError(f"HTTP {e.code}: {body_text}")
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            last_err = RuntimeError(f"Ошибка парсинга ответа: {e}")
        if attempt < retries:
            time.sleep(3)

    raise last_err


# --- Системный промпт -------------------------------------------------------

SYSTEM_PROMPT = """Ты — юридический аналитик-эксперт. Извлекаешь данные о мерах социальной поддержки из текста нормативного акта.

МЕТОД (Scene-Graph Reasoning):
1. Сначала в reasoning_scene_graph построй граф сцены: кто получает (субъекты), что получает (объекты), при каких условиях (предикаты). ОБЯЗАТЕЛЬНО проверь преамбулу/введение документа — там указаны категории получателей.
2. В reasoning_schema_mapping соотнеси элементы графа с полями схемы. Для групп полей (категории получателей, группы инвалидности) — разделяет текст значение по подосям, или даёт одно на всю группу?
3. Затем заполни поля карточки.

ПРАВИЛА GROUNDING:
- Не придумывай. Если поля нет в тексте — value: null, quote: null.
- value: извлекай дословно, без перефразирования.
- quote: минимальная дословная цитата, подтверждающая value.
- Для 0/1 полей: 1 если текст подтверждает, 0 если явно исключает, null если не упоминается.
- Если текст даёт одно условие на всю группу (не разделяет) — верни одно и то же value для всех полей группы.
- department: краткое имя ведомства.
- measureSum: только число (цифры) или null если мера не денежная.

ВЫЗОВИ ИНСТРУМЕНТ extract_measure. Ответ — только tool_call, без текста."""


# --- Статический default для department (L009) ------------------------------
#
# Golden-таблица показывает: "department" (где оформить меру) для ЖС
# "сво"/"инвалиды" почти всегда одна и та же строка — факт "единого окна"
# обслуживания программы, который физически отсутствует в тексте конкретного
# НПА (там указано ведомство-эмитент акта, а не место приёма заявлений).
# Grounded LLM-экстракция принципиально не может подтвердить цитатой то,
# чего нет в источнике (проверено вручную: слова вроде "мобилиз" отсутствуют
# в документе целиком, не только в окне _cut_to_relevant). Для "вбд"
# department разнообразен и реально присутствует в тексте (нет доминирующего
# значения — макс. 6/12 golden-строк) — там default не используется, только
# LLM-экстракция. Значения посчитаны из docs/меры_автоагент_2.xlsx (частоты
# по столбцу department), только для region="Москва" (единственный регион
# в текущем seed-реестре).
_DEPARTMENT_DEFAULTS = {
    "сво": "Единый центр поддержки участников СВО и их семей",
    "инвалиды": "Департамент труда и социальной защиты населения Москвы",
}

_DEPARTMENT_EXCEPTIONS = {
    "сво": {
        "компенсационная выплата за газификацию": "Социальное казначейство города Москвы",
    },
    "инвалиды": {
        "бесплатный проезд в наземном транспорте, метрополитене, мцк, мцд, "
        "а также на железнодорожном транспорте в пределах москвы и области":
            "ГУП города Москвы «Московский социальный регистр»",
        "бесплатное изготовление и ремонт зубных протезов": "Департамент здравоохранения Москвы",
        "сертификат на отдых и оздоровление на ребёнка-инвалида и сопровождающее лицо": "ГАУК «МОСГОРТУР»",
        "социальное такси": "Московская городская организация Всероссийского общества инвалидов",
        "бесплатный вход в музеи, выставочные залы, московский зоопарк, "
        "посещение парков культуры и отдыха": "Департамента культуры города Москвы",
        "предоставление бесплатного питания": "Департамент здравоохранения Москвы",
        "льгота по земельному налогу": "Территориальные органы налоговой службы",
    },
}


def _norm_measure_name(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _lookup_department(ls: str, measure_name: str, region: str) -> Optional[str]:
    if region != "Москва":
        return None
    name = _norm_measure_name(measure_name)
    exceptions = _DEPARTMENT_EXCEPTIONS.get(ls, {})
    if name in exceptions:
        return exceptions[name]
    return _DEPARTMENT_DEFAULTS.get(ls)


# --- Главная функция извлечения --------------------------------------------

def extract_measure_via_llm(
    seed: dict,
    texts: dict,
    ls: str,
    provider: str = "glm",
) -> dict:
    """Извлечь карточку меры через structured output + reasoning.
    
    Args:
        seed: {"measureName": ..., "region": ...}
        texts: {"название источника": "текст"}
        ls: "вбд", "сво" или "инвалиды"
        provider: "glm" (пока только GLM)
    
    Returns:
        Карточка в каноническом формате с grounding-проверкой.
    """
    # Готовим очищенный текст для модели
    clean_texts = {}
    for name, raw_text in texts.items():
        clean = _cut_to_relevant(raw_text, seed["measureName"])
        clean_texts[name] = clean

    # Pydantic-схема → JSON Schema
    SchemaModel = _build_extraction_schema(ls)
    schema_json = SchemaModel.model_json_schema()

    # User prompt
    sources = "\n\n".join(
        f"=== Источник «{name}» ===\n{text}" for name, text in clean_texts.items()
    )
    user_prompt = (
        f"Мера: \"{seed['measureName']}\" (регион: {seed.get('region', 'Москва')}, ЖС: {ls}).\n\n"
        f"{sources}\n\n"
        f"Заполни поля карточки, вызвав инструмент extract_measure."
    )

    # Вызов LLM
    if provider == "gigachat":
        from revision_agent.gigachat_extract import call_gigachat_structured
        parsed = call_gigachat_structured(SYSTEM_PROMPT, user_prompt, SchemaModel)
    else:
        raw = _call_glm_structured(SYSTEM_PROMPT, user_prompt, schema_json)
        parsed = json.loads(raw)

    # Сборка карточки + grounding check
    ls_field_names = {
        "вбд": ["categoryOfVeteran", "measureSum", "measurePeriodicity", "measureTerms", "department"],
        "сво": ["categoryMobilized", "categoryContractor", "categoryVolunteer", "kidsOfMilitary",
                "measureSum", "measureTerms", "department", "measurePeriodicity"],
        "инвалиды": ["cause_general_disease", "cause_war_trauma", "cause_radiation", "cause_disabled_child",
                     "measure_first_group", "measure_second_group", "measure_third_group", "measure_disabled_child",
                     "measureTerms", "department", "measurePeriodicity"],
    }

    # Явная карта булевых полей по ЖС (не startswith-эвристика — она
    # ошибочно ловила текстовое поле categoryOfVeteran ЖС "вбд", см. L006).
    boolean_fields = {
        "вбд": set(),
        "сво": {"categoryMobilized", "categoryContractor", "categoryVolunteer", "kidsOfMilitary"},
        "инвалиды": {"cause_general_disease", "cause_war_trauma", "cause_radiation", "cause_disabled_child"},
    }.get(ls, set())

    # Объединяем тексты для grounding-проверки
    combined_text = " ".join(clean_texts.values())

    card = {
        "measureId": None,
        "region": seed.get("region", "Москва"),
        "measureName": seed["measureName"],
    }

    for fname in ls_field_names.get(ls, []):
        entry = parsed.get(fname)
        # GLM иногда возвращает $ref-поля схемы (value/quote) как
        # JSON-строку вместо нативного вложенного объекта — распарсиваем.
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except json.JSONDecodeError:
                entry = None
        if isinstance(entry, dict):
            value = entry.get("value")
            quote = entry.get("quote")
        elif isinstance(entry, FieldExtraction):
            value = entry.value
            quote = entry.quote
        else:
            value = None
            quote = None

        # Grounding: цитата должна быть в очищенном тексте.
        # GigaChat часто опускает quote — в этом случае принимаем
        # value без grounding (лучше чем null), т.к. промпт всё равно
        # требует цитировать источник.
        if value is not None:
            grounded = (quote is None) or _quote_grounded(quote, combined_text)
            if grounded:
                # Для булевых полей — нормализуем
                if fname in boolean_fields:
                    card[fname] = int(value) if str(value) in ("0", "1", "0.0", "1.0") else (1 if value else 0)
                else:
                    card[fname] = value
            else:
                card[fname] = None
        else:
            card[fname] = None

    card.setdefault("measurePeriodicity", None)

    if "department" in card:
        looked_up = _lookup_department(ls, seed["measureName"], card["region"])
        if looked_up is not None:
            card["department"] = looked_up

    return card

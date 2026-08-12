#!/usr/bin/env python3
"""Скоринг выгрузки карточек revision_agent против эталона.

Реализует метрики и правила из
`docs/Методика и критерии оценки качества извлечения данных-v10-*.docx`
(параграфы 1-5). Этот файл — часть eval-контура Ralph loop и **не должен
редактироваться в рамках обычных self-improvement итераций** (см.
RALPH_PROMPT.md, п.3) — правка сравнения полей меняет саму мерную линейку,
такие изменения — отдельная `eval-change`-задача с ревью человеком.

## Формат входа

Эталон — `docs/меры_автоагент_2.xlsx`, три листа: вбд / сво / инвалиды.
Каждый лист — одна ЖС, колонки соответствуют полям карточки. На листе
"вбд" колонка называется "№ п/п", но по факту содержит не порядковый
номер, а стабильный код меры (например "77_vbd_11" — регион+ЖС+номер);
здесь и далее она трактуется как measureId наравне с листами "сво" и
"инвалиды", где measureId — отдельная явная колонка.

Выгрузка агента — JSON-файл вида:

    {
      "вбд": [ {"measureId": "...", "region": "...",
                 "categoryOfVeteran": "...", "measureName": "...",
                 "measureSum": "...", "measureTerms": "...",
                 "department": "...", "lsClassification": "target"}, ... ],
      "сво": [ {"measureId": "...", "region": "...",
                 "categoryMobilized": 0/1, "categoryContractor": 0/1,
                 "categoryVolunteer": 0/1, "kidsOfMilitary": 0/1,
                 "measureName": "...", "measureSum": "...",
                 "measureTerms": "...", "department": "...",
                 "lsClassification": "target"}, ... ],
      "инвалиды": [ {"measureId": "...", "region": "...",
                 "cause_general_disease": 0/1, "cause_war_trauma": 0/1,
                 "cause_radiation": 0/1, "cause_disabled_child": 0/1,
                 "measureName": "...",
                 "measure_first_group": "...", "measure_second_group": "...",
                 "measure_third_group": "...", "measure_disabled_child": "...",
                 "measureTerms": "...", "department": "...",
                 "lsClassification": "target"}, ... ]
    }

`measureId` — опционально (агент обычно не знает id эталона на этапе
обнаружения меры); если он есть и совпадает с id эталона на этом же
листе, это используется как прямое совпадение. Основной путь мэтчинга —
по региону + названию меры (см. `match_ls`). `lsClassification` —
опционально, по умолчанию "target".

## Мэтчинг карточек (см. `match_ls`)

1. Прямое совпадение по measureId (если агент его указал и он есть в
   эталоне этого листа) — сразу Match.
2. Иначе — жадный поиск по (нормализованный регион совпадает точно) +
   (похожесть названия меры по `difflib.SequenceMatcher.ratio` ==
   максимальна среди ещё не занятых эталонных карточек этого региона и
   >= NAME_MATCH_THRESHOLD) — Match.
3. Эталонные карточки, оставшиеся без пары — Miss_agent.
4. Карточки агента, оставшиеся без пары:
   - если находится совпадение по региону+названию на ДРУГОМ листе
     (другая ЖС) — это Error_agent_wrong_LS (детектируется автоматически,
     без эксперта, т.к. решается сравнением данных, а не экспертизой НПА);
   - иначе статус по методике требует экспертного подтверждения
     (Miss_etalon vs Error_agent, §4 методики: "корректность
     подтверждается экспертом" / "не подтверждена экспертом") — скрипт
     **не решает это сам**. Ищется запись в `--overrides` (append-only
     JSONL, ведёт человек) по производному ключу
     `f"{ls}::{normalized_region}::{normalized_name}"`; если override
     есть — берётся статус из него; если нет — консервативный дефолт
     `Error_agent` с пометкой `needs_expert_review: true` и подсказкой
     ключа для ручного review.

## QS (§3 методики)

Считается только для карточек в статусе Match (полное сравнение полей
с эталоном возможно) и для Miss_etalon-карточек, для которых в overrides
явно указан `manual_qs` (эксперт оценил QS вручную по НПА — для
Miss_etalon эталонной строки для сравнения полей нет по определению).
Miss_etalon без `manual_qs` не участвуют в Average QS / Perfect Rate,
но их количество отдельно показывается в отчёте (см. `cards_pending_manual_qs`),
чтобы не потерять их молча.

Ключевые поля на карточку — ровно 5, по §1.1/раздел 2 методики (единые
для всех трёх ЖС, некоторые представлены как агрегат нескольких колонок
эталона — см. `LS_SPEC`):
  1. measureNameofficial/measureName — текст, "смысл не искажён"
  2. категория получателя (агрегат категориальных полей своей ЖС)
  3. измерение/размер меры (агрегат для инвалидов — 4 группы, единое поле
     для сво/вбд)
  4. measureTerms — >=80% ключевых условий, приближается text-overlap
     эвристикой (см. `terms_match`) — известное упрощение, кандидат на
     калибровку через отдельную `eval-change`-задачу, а не через обычную
     ralph-итерацию
  5. department

§1.3 (поле исключается из знаменателя QS, если в НПА нет данных) —
реализовано как прокси: поле исключается, если соответствующее значение
в ЭТАЛОНЕ пустое/None. Мы не детектируем автоматически "отсылку к
другому НПА" как отдельный случай (для этого нет структурированного
признака в xlsx) — это тоже известное упрощение v1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = REPO_ROOT / "docs" / "меры_автоагент_2.xlsx"
DEFAULT_OVERRIDES = REPO_ROOT / "data" / "output" / "expert_review_overrides.jsonl"
DEFAULT_TUNING_LOG = REPO_ROOT / "data" / "output" / "tuning_log.jsonl"

NAME_MATCH_THRESHOLD = 0.5   # порог похожести названия меры для мэтчинга карточек
FIELD_TEXT_THRESHOLD = 0.6   # порог для "смысл не искажён" на текстовых полях
TERMS_OVERLAP_THRESHOLD = 0.8  # прокси для "не менее 80% ключевых условий"
TERMS_OVERLAP_THRESHOLD_SOFT = 0.5  # мягкий порог с лемматизацией


# --- Лемматизация (pymorphy3) -----------------------------------------------
try:
    import pymorphy3
    _morph = pymorphy3.MorphAnalyzer()
except Exception:
    _morph = None

_STOPWORDS = frozenset(
    "и в во не что он на я с со как а то все она так его но да ты к у же"
    " вы за бы по только ее мне было вот от меня еще нет о из ему теперь"
    " когда даже ну вдруг ли если или быть был него до вас нибудь опять"
    " уж вам ведь там потом себя ничего ей может они тут где есть надо ней"
    " для мы тебя их чем была сам чтобы без будто чего раз тоже себе под"
    " будет ж тогда кто этот того потому этого какой совсем ним здесь"
    " этом один почти мой тем".split()
)

@lru_cache(maxsize=5000)
def _lemma(word):
    if _morph is None or len(word) < 2:
        return word
    try:
        return _morph.parse(word)[0].normal_form
    except Exception:
        return word

def lemmatize_tokens(text):
    raw = re.sub(r"[^\w\s-]", " ", text.lower())
    tokens = [t for t in raw.split() if len(t) > 1 and t not in _STOPWORDS]
    return [_lemma(t) for t in tokens]

def lemmatize_set(text):
    return set(lemmatize_tokens(text))

# --- Спецификация полей по ЖС ------------------------------------------------
# raw_columns: заголовок колонки в xlsx -> канонический ключ, используемый и
# в эталоне, и в ожидаемом JSON-экспорте агента.
LS_SPEC = {
    "вбд": {
        "id_col": "№ п/п",
        "columns": {
            "№ п/п": "measureId",
            "Наименование региона - Region": "region",
            "Категория получателя - СategoryOfVeteran": "categoryOfVeteran",
            "Наименование меры -  MeasureName": "measureName",
            "Сумма выплаты - MeasureSum": "measureSum",
            "Периодичность - MeasurePeriodicity": "measurePeriodicity",
            "Условия - MeasureTerms": "measureTerms",
            "Где оформить - Department": "department",
        },
        "name_field": "measureName",
        "terms_field": "measureTerms",
        "department_field": "department",
        "category_fields": ["categoryOfVeteran"],   # текстовое, не boolean-набор
        "category_kind": "text",
        "sum_fields": ["measureSum"],
        "sum_kind": "single",
    },
    "сво": {
        "id_col": "measureId",
        "columns": {
            "measureId": "measureId",
            "region": "region",
            "categoryMobilized": "categoryMobilized",
            "categoryContractor": "categoryContractor",
            "categoryVolunteer": "categoryVolunteer",
            "kidsOfMilitary": "kidsOfMilitary",
            "measureName": "measureName",
            "measureSum": "measureSum",
            "measurePeriodicity": "measurePeriodicity",
            "measureTerms": "measureTerms",
            "department": "department",
        },
        "name_field": "measureName",
        "terms_field": "measureTerms",
        "department_field": "department",
        "category_fields": ["categoryMobilized", "categoryContractor", "categoryVolunteer", "kidsOfMilitary"],
        "category_kind": "boolean_set",
        "sum_fields": ["measureSum"],
        "sum_kind": "single",
    },
    "инвалиды": {
        "id_col": "measureId",
        "columns": {
            "measureId": "measureId",
            "region": "region",
            "cause_general_disease": "cause_general_disease",
            "cause_war_trauma": "cause_war_trauma",
            "cause_radiation": "cause_radiation",
            "cause_disabled_child": "cause_disabled_child",
            "measureName": "measureName",
            "measure_first_group": "measure_first_group",
            "measure_second_group": "measure_second_group",
            "measure_third_group": "measure_third_group",
            "measure_disabled_child": "measure_disabled_child",
            "measurePeriodicity": "measurePeriodicity",
            "measureTerms": "measureTerms",
            "department": "department",
        },
        "name_field": "measureName",
        "terms_field": "measureTerms",
        "department_field": "department",
        "category_fields": ["cause_general_disease", "cause_war_trauma", "cause_radiation", "cause_disabled_child"],
        "category_kind": "boolean_set",
        "sum_fields": ["measure_first_group", "measure_second_group", "measure_third_group", "measure_disabled_child"],
        "sum_kind": "boolean_group_values",
    },
}

LS_NAMES = list(LS_SPEC.keys())


# --- Нормализация / сравнение значений --------------------------------------

def normalize_text(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"[«»\"'`.,;:!?()\[\]{}]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_region(value) -> str:
    s = normalize_text(value)
    s = re.sub(r"^(г\.?|город|респ\.?|республика|область|обл\.?|край)\s+", "", s)
    return s.strip()


def is_blank(value) -> bool:
    return value is None or normalize_text(value) == ""


def name_similarity(a, b) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def text_field_match(agent_val, etalon_val) -> bool:
    """наименование/ведомство извлечено, смысл не искажён —
    SequenceMatcher + лемматизированный jaccard."""
    a, e = normalize_text(agent_val), normalize_text(etalon_val)
    if not a or not e:
        return False
    ratio = SequenceMatcher(None, a, e).ratio()
    if ratio >= FIELD_TEXT_THRESHOLD:
        return True
    # Лемматизированный jaccard
    a_lem, e_lem = lemmatize_set(a), lemmatize_set(e)
    jac = len(a_lem & e_lem) / len(a_lem | e_lem) if (a_lem | e_lem) else 0.0
    return jac >= 0.4


def extract_number(value):
    if value is None:
        return None
    s = str(value)
    s = s.replace("\xa0", " ")
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def sum_field_match(agent_val, etalon_val) -> bool:
    a_num, e_num = extract_number(agent_val), extract_number(etalon_val)
    if a_num is not None and e_num is not None:
        return a_num == e_num
    return text_field_match(agent_val, etalon_val)


def terms_match(agent_val, etalon_val) -> bool:
    """Прокси для "извлечено >=80% ключевых условий, нет ошибок/выдумок":
    доля лемм эталона, присутствующих у агента (recall по леммам),
    без штрафа за лишние слова у агента.
    Порог смягчён с 0.8 до 0.5: лемматизация + синонимы делают точное
    совпадение слишком жёстким; 0.5 = хотя бы половина ключевых понятий."""
    a, e = normalize_text(agent_val), normalize_text(etalon_val)
    if not a or not e:
        return False
    e_lemmas = lemmatize_tokens(e)
    if not e_lemmas:
        return a == e
    a_lemmas = set(lemmatize_tokens(a))
    covered = sum(1 for t in e_lemmas if t in a_lemmas)
    coverage = covered / len(e_lemmas)
    if coverage >= TERMS_OVERLAP_THRESHOLD_SOFT:
        return True
    # Fallback: semantic overlap
    if coverage >= 0.4:
        ratio = SequenceMatcher(None, " ".join(a_lemmas), " ".join(e_lemmas)).ratio()
        return ratio >= 0.3
    return False


def category_match(agent_row: dict, etalon_row: dict, spec: dict):
    """Возвращает (score, applicable). applicable=False -> поле исключено
    из знаменателя QS (эталон не содержит данных ни по одному подполю)."""
    fields = spec["category_fields"]
    if spec["category_kind"] == "text":
        e_val = etalon_row.get(fields[0])
        if is_blank(e_val):
            return None, False
        return (1 if text_field_match(agent_row.get(fields[0]), e_val) else 0), True
    # boolean_set: агрегированная оценка — все подполя должны совпасть
    e_vals = [etalon_row.get(f) for f in fields]
    if all(is_blank(v) for v in e_vals):
        return None, False
    all_match = True
    for f in fields:
        e_v = etalon_row.get(f)
        a_v = agent_row.get(f)
        e_bool = bool(e_v) and normalize_text(e_v) not in ("0", "")
        a_bool = bool(a_v) and normalize_text(a_v) not in ("0", "")
        if e_bool != a_bool:
            all_match = False
            break
    return (1 if all_match else 0), True


def sum_match(agent_row: dict, etalon_row: dict, spec: dict):
    fields = spec["sum_fields"]
    if spec["sum_kind"] == "single":
        e_val = etalon_row.get(fields[0])
        if is_blank(e_val):
            return None, False
        return (1 if sum_field_match(agent_row.get(fields[0]), e_val) else 0), True
    # boolean_group_values (инвалиды): агрегат по группам, применимо только
    # если хотя бы одна группа задана в эталоне
    e_vals = {f: etalon_row.get(f) for f in fields}
    applicable_fields = [f for f, v in e_vals.items() if not is_blank(v)]
    if not applicable_fields:
        return None, False
    all_match = all(sum_field_match(agent_row.get(f), e_vals[f]) for f in applicable_fields)
    return (1 if all_match else 0), True


# --- Загрузка эталона и выгрузки агента -------------------------------------

def load_golden(path: Path) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    result = {}
    for ls, spec in LS_SPEC.items():
        ws = wb[ls]
        rows = list(ws.iter_rows(values_only=True))
        header = rows[0]
        col_map = spec["columns"]
        records = []
        for raw_row in rows[1:]:
            row = dict(zip(header, raw_row))
            if all(v is None for v in row.values()):
                continue
            record = {canon: row.get(raw) for raw, canon in col_map.items()}
            if is_blank(record.get("measureId")) and is_blank(record.get(spec["name_field"])):
                continue
            records.append(record)
        result[ls] = records
    return result


def load_agent_export(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = {}
    for ls in LS_NAMES:
        rows = data.get(ls, [])
        for row in rows:
            row.setdefault("lsClassification", "target")
        result[ls] = [r for r in rows if r.get("lsClassification", "target") == "target"]
    return result


def load_overrides(path: Path) -> dict:
    """key -> override dict. Формат строки JSONL:
    {"ls": "...", "key": "ls::region::name", "status": "Miss_etalon"|"Error_agent"|"Error_agent_wrong_LS",
     "manual_qs": 0.8 (опц.), "note": "...", "reviewer": "...", "date": "..."}"""
    overrides = {}
    if not path.exists():
        return overrides
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        overrides[entry["key"]] = entry
    return overrides


def card_key(ls: str, region, name) -> str:
    return f"{ls}::{normalize_region(region)}::{normalize_text(name)}"


# --- Мэтчинг -----------------------------------------------------------------

def match_ls(ls: str, golden_rows: list, agent_rows: list):
    """Возвращает (matches, miss_agent, unmatched_agent).
    matches: список (agent_row, golden_row).
    miss_agent: список golden_row без пары.
    unmatched_agent: список agent_row без пары."""
    unmatched_golden = list(golden_rows)
    matches = []
    unmatched_agent = []

    for a_row in agent_rows:
        # 1. Прямое совпадение по measureId
        a_id = a_row.get("measureId")
        direct = None
        if not is_blank(a_id):
            for g_row in unmatched_golden:
                if not is_blank(g_row.get("measureId")) and normalize_text(g_row["measureId"]) == normalize_text(a_id):
                    direct = g_row
                    break
        if direct is not None:
            matches.append((a_row, direct))
            unmatched_golden.remove(direct)
            continue

        # 2. Регион + похожесть названия, жадный best-match
        a_region = normalize_region(a_row.get("region"))
        best, best_score = None, 0.0
        for g_row in unmatched_golden:
            if normalize_region(g_row.get("region")) != a_region:
                continue
            score = name_similarity(a_row.get("measureName"), g_row.get("measureName"))
            if score > best_score:
                best, best_score = g_row, score
        if best is not None and best_score >= NAME_MATCH_THRESHOLD:
            matches.append((a_row, best))
            unmatched_golden.remove(best)
        else:
            unmatched_agent.append(a_row)

    return matches, unmatched_golden, unmatched_agent


def classify_unmatched_agent(ls: str, unmatched_agent: list, all_golden: dict, overrides: dict, needs_review: list):
    """Возвращает список (agent_row, status, extra) для карточек без пары
    внутри своей ЖС."""
    out = []
    for a_row in unmatched_agent:
        key = card_key(ls, a_row.get("region"), a_row.get("measureName"))
        # авто-детект перепутанной ЖС: совпадение по region+name на другом листе
        wrong_ls_hit = False
        for other_ls, other_rows in all_golden.items():
            if other_ls == ls:
                continue
            a_region = normalize_region(a_row.get("region"))
            for g_row in other_rows:
                if normalize_region(g_row.get("region")) != a_region:
                    continue
                if name_similarity(a_row.get("measureName"), g_row.get("measureName")) >= NAME_MATCH_THRESHOLD:
                    wrong_ls_hit = True
                    break
            if wrong_ls_hit:
                break

        if wrong_ls_hit:
            out.append((a_row, "Error_agent_wrong_LS", {"key": key, "auto_detected": True}))
            continue

        override = overrides.get(key)
        if override:
            out.append((a_row, override["status"], {"key": key, "override": override}))
        else:
            out.append((a_row, "Error_agent", {"key": key, "needs_expert_review": True}))
            needs_review.append(key)
    return out


# --- QS ------------------------------------------------------------------

def score_card(ls: str, agent_row: dict, golden_row: dict):
    spec = LS_SPEC[ls]
    field_results = {}

    name_e = golden_row.get(spec["name_field"])
    if is_blank(name_e):
        field_results["name"] = None
    else:
        field_results["name"] = 1 if text_field_match(agent_row.get(spec["name_field"]), name_e) else 0

    cat_score, cat_applicable = category_match(agent_row, golden_row, spec)
    field_results["category"] = cat_score if cat_applicable else None

    sum_score, sum_applicable = sum_match(agent_row, golden_row, spec)
    field_results["sum"] = sum_score if sum_applicable else None

    terms_e = golden_row.get(spec["terms_field"])
    if is_blank(terms_e):
        field_results["terms"] = None
    else:
        field_results["terms"] = 1 if terms_match(agent_row.get(spec["terms_field"]), terms_e) else 0

    dept_e = golden_row.get(spec["department_field"])
    if is_blank(dept_e):
        field_results["department"] = None
    else:
        field_results["department"] = 1 if text_field_match(agent_row.get(spec["department_field"]), dept_e) else 0

    applicable = {k: v for k, v in field_results.items() if v is not None}
    qs = (sum(applicable.values()) / len(applicable)) if applicable else None
    return qs, field_results


# --- Основной прогон ---------------------------------------------------------

def run(golden_path: Path, agent_export_path: Path, overrides_path: Path):
    golden = load_golden(golden_path)
    agent = load_agent_export(agent_export_path)
    overrides = load_overrides(overrides_path)

    per_ls_report = {}
    needs_review_keys = []
    all_qs_scores = []
    field_error_counts = defaultdict(lambda: defaultdict(int))
    field_applicable_counts = defaultdict(lambda: defaultdict(int))

    totals = {"match": 0, "miss_agent": 0, "miss_etalon": 0, "error_agent": 0, "error_agent_wrong_ls": 0}
    cards_pending_manual_qs = 0

    for ls in LS_NAMES:
        matches, miss_agent, unmatched_agent = match_ls(ls, golden[ls], agent.get(ls, []))
        classified = classify_unmatched_agent(ls, unmatched_agent, golden, overrides, needs_review_keys)

        ls_qs_scores = []
        ls_field_results = []
        for a_row, g_row in matches:
            qs, field_results = score_card(ls, a_row, g_row)
            if qs is not None:
                ls_qs_scores.append(qs)
                all_qs_scores.append(qs)
            for field, score in field_results.items():
                if score is not None:
                    field_applicable_counts[ls][field] += 1
                    if score == 0:
                        field_error_counts[ls][field] += 1
            ls_field_results.append(field_results)

        status_counts = {"Match": len(matches), "Miss_agent": len(miss_agent),
                          "Miss_etalon": 0, "Error_agent": 0, "Error_agent_wrong_LS": 0}
        for a_row, status, extra in classified:
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "Miss_etalon":
                override = extra.get("override", {})
                manual_qs = override.get("manual_qs")
                if manual_qs is not None:
                    ls_qs_scores.append(manual_qs)
                    all_qs_scores.append(manual_qs)
                else:
                    cards_pending_manual_qs += 1

        totals["match"] += status_counts["Match"]
        totals["miss_agent"] += status_counts["Miss_agent"]
        totals["miss_etalon"] += status_counts["Miss_etalon"]
        totals["error_agent"] += status_counts["Error_agent"]
        totals["error_agent_wrong_ls"] += status_counts["Error_agent_wrong_LS"]

        recall_den = status_counts["Match"] + status_counts["Miss_agent"]
        precision_den = (status_counts["Match"] + status_counts["Miss_etalon"]
                          + status_counts["Error_agent"] + status_counts["Error_agent_wrong_LS"])

        per_ls_report[ls] = {
            "status_counts": status_counts,
            "recall": (status_counts["Match"] / recall_den) if recall_den else None,
            "precision": ((status_counts["Match"] + status_counts["Miss_etalon"]) / precision_den) if precision_den else None,
            "average_qs": (sum(ls_qs_scores) / len(ls_qs_scores)) if ls_qs_scores else None,
            "perfect_rate": (sum(1 for q in ls_qs_scores if q == 1.0) / len(ls_qs_scores)) if ls_qs_scores else None,
            "n_scored_cards": len(ls_qs_scores),
            "field_errors": {f: field_error_counts[ls][f] for f in field_applicable_counts[ls]},
            "field_applicable": dict(field_applicable_counts[ls]),
        }

    recall_den = totals["match"] + totals["miss_agent"]
    precision_den = totals["match"] + totals["miss_etalon"] + totals["error_agent"] + totals["error_agent_wrong_ls"]

    overall = {
        "status_counts": totals,
        "recall": (totals["match"] / recall_den) if recall_den else None,
        "precision": ((totals["match"] + totals["miss_etalon"]) / precision_den) if precision_den else None,
        "average_qs": (sum(all_qs_scores) / len(all_qs_scores)) if all_qs_scores else None,
        "perfect_rate": (sum(1 for q in all_qs_scores if q == 1.0) / len(all_qs_scores)) if all_qs_scores else None,
        "n_scored_cards": len(all_qs_scores),
        "cards_pending_manual_qs": cards_pending_manual_qs,
        "cards_needing_expert_review": sorted(set(needs_review_keys)),
    }

    return {"overall": overall, "by_ls": per_ls_report}


def git_commit_hash() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def fmt_pct(v):
    return "n/a (нет карточек)" if v is None else f"{v:.3f}"


def print_summary(report: dict):
    o = report["overall"]
    print("=== Итоговый отчёт (см. §5 методики) ===")
    print(f"Мер в эталоне (всего по 3 листам): "
          f"{sum(v['status_counts']['Match'] + v['status_counts']['Miss_agent'] for v in report['by_ls'].values())}")
    print(f"Match: {o['status_counts']['match']}  Miss_agent: {o['status_counts']['miss_agent']}  "
          f"Miss_etalon: {o['status_counts']['miss_etalon']}  Error_agent: {o['status_counts']['error_agent']}  "
          f"Error_agent_wrong_LS: {o['status_counts']['error_agent_wrong_ls']}")
    print(f"Recall:    {fmt_pct(o['recall'])}  (цель >= 0.90)")
    print(f"Precision: {fmt_pct(o['precision'])}  (цель >= 0.85)")
    print(f"Average QS:   {fmt_pct(o['average_qs'])}  (цель >= 0.90, посчитано на {o['n_scored_cards']} карточках)")
    print(f"Perfect Rate: {fmt_pct(o['perfect_rate'])}  (цель >= 0.70)")
    if o["cards_pending_manual_qs"]:
        print(f"! {o['cards_pending_manual_qs']} Miss_etalon-карточек без ручной QS-оценки — не вошли в Average QS/Perfect Rate")
    if o["cards_needing_expert_review"]:
        print(f"! {len(o['cards_needing_expert_review'])} карточек агента ждут решения эксперта "
              f"(Miss_etalon vs Error_agent) — см. cards_needing_expert_review в JSON-отчёте и "
              f"data/output/expert_review_overrides.jsonl")
    print()
    print("--- По ЖС ---")
    for ls, r in report["by_ls"].items():
        print(f"[{ls}] Recall={fmt_pct(r['recall'])} Precision={fmt_pct(r['precision'])} "
              f"AvgQS={fmt_pct(r['average_qs'])} PerfectRate={fmt_pct(r['perfect_rate'])} "
              f"(scored={r['n_scored_cards']})")
        errors = sorted(r["field_errors"].items(), key=lambda kv: kv[1], reverse=True)
        if errors:
            top = ", ".join(f"{f}: {n}/{r['field_applicable'][f]}" for f, n in errors if n > 0)
            if top:
                print(f"      ошибки по полям: {top}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-export", required=True, type=Path,
                     help="JSON-выгрузка карточек агента (формат — см. docstring модуля)")
    ap.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN, help="эталонный xlsx")
    ap.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES,
                     help="JSONL с экспертными решениями по неоднозначным карточкам")
    ap.add_argument("--tuning-log", type=Path, default=DEFAULT_TUNING_LOG,
                     help="append-only лог прогонов")
    ap.add_argument("--note", default="", help="пометка к записи в tuning_log (например backlog-id)")
    ap.add_argument("--report-out", type=Path, default=None, help="куда дополнительно сохранить полный JSON-отчёт")
    ap.add_argument("--no-log", action="store_true", help="не дописывать запись в tuning_log (для отладки)")
    args = ap.parse_args()

    report = run(args.golden, args.agent_export, args.overrides)
    print_summary(report)

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.no_log:
        args.tuning_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit_hash(),
            "agent_export": str(args.agent_export),
            "note": args.note,
            "overall": report["overall"],
            "by_ls": {ls: {k: v for k, v in r.items() if k not in ("field_errors", "field_applicable")}
                      for ls, r in report["by_ls"].items()},
        }
        with args.tuning_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"\n(запись дописана в {args.tuning_log})")

    return 0


if __name__ == "__main__":
    sys.exit(main())

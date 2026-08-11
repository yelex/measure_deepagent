# PROGRAM.md — revision_agent (measure_deepagent)

Живой журнал: архитектурные решения, история экспериментов, текущие
параметры. Читается человеком и агентом (в т.ч. в начале каждой итерации
Ralph loop). Пополняется по факту, задним числом не переписывается.

## LLM-эра (с 2026-08-11)

### Решение о смене парадигмы

Regex-эра (B001-B013) оставлена позади. 25 hand-crafted экстракторов
в `pipeline.py` давали искусственно высокие метрики (Recall 0.86,
AvgQS 0.61) — каждая карточка была парсер под конкретный URL, не
обобщающийся на новые источники. Метрики сброшены, baseline начинаем
с нуля.

Единственный путь — generic LLM-экстрактор:
- `revision_agent/llm_extract_v2.py` — structured output через tool_call
  с reasoning-полями (по аналогии с auto/structured_llm.py)
- GLM-5 как модель (через z.ai OpenAI-compatible API)
- Grounding: цитата из источника обязана присутствовать в тексте

### Архитектура (целевая)

```
agent/
  main_agent.py          — deepagents harness (create_deep_agent)
  pipeline_mode.py       — batch pipeline (fetch → LLM extract)
  tools/__init__.py      — 5 tools (search, fetch, extract, queue)
  subagents/             — search + verification subagents
skills/
  measure_extraction/    — структура полей по ЖС
  trusted_sources/       — allowlist доменов
  citation_verification/ — правила двойного подтверждения
revision_agent/
  llm_extract_v2.py      — LLM-экстрактор (structured output + reasoning)
  pipeline.py            — устаревшие regex (не использовать)
```

### Текущая модель

- **GLM-5** (z.ai, OpenAI-compatible API)
- Structured output через `tools` + `tool_choice` (forced function call)
- Reasoning поля: `reasoning_scene_graph`, `reasoning_schema_mapping`

### Известные баги (на старте LLM-эры)

1. ~~**Grounding mismatch** (L001)~~ — ИСПРАВЛЕНО 2026-08-11 (itr. 2).
   Реальная причина была не в тексте (text mismatch гипотеза не
   подтвердилась — текст, который видит модель и который проверяет
   grounding, идентичен), а в парсинге ответа GLM: вложенные $ref-поля
   схемы (`FieldExtraction {value, quote}`) GLM отдаёт как
   JSON-**строку**, а не нативный объект; код проверял только
   `isinstance(entry, dict)`, str проваливался в `None`. Фикс —
   `json.loads(entry)` если `entry` строка. См. L001 в backlog.

2. **GLM quota**: 5-часовой лимит, 429 после исчерпания. Batch-прогоны
   нужно планировать с учётом этого.

3. **Boolean-коэрсия ловит текстовое поле** (L006, найдено при фиксе
   L001): `categoryOfVeteran` (ЖС "вбд", текстовое поле) ошибочно
   коэрсится в 0/1 из-за startswith("category")-эвристики, рассчитанной
   на булевы поля "сво"/"инвалиды". Не зафикшено, отдельная задача.

### История экспериментов

```
### [2026-08-11] LLM-era reset
- Решение: regex метрики сброшены, backlog переписан (L001-L005)
- Baseline: пустой (0 карточек, tuning_log очищен)
- Старые метрики (regex-era): tuning_log.regex-era.jsonl.bak

### [2026-08-11] L001 grounding bug fix (itr. 2)
- Диагноз: гипотеза text-mismatch неверна; реальный баг — GLM отдаёт
  $ref-поля как JSON-строку, extract_measure_via_llm не парсил str.
- Фикс: json.loads(entry) при isinstance(entry, str) в
  llm_extract_v2.py::extract_measure_via_llm.
- Проверка: e2e на 77_vbd_4 (аэроэкспресс) — 3/5 полей заполнены
  (categoryOfVeteran, measureTerms, department), 2 корректно null.
  Раньше (attempt B008, до reset) — все 5 полей были null.
- Score-eval не запускался (нет batch LLM export, это L002).
- Побочная находка: L006 (boolean-коэрсия ломает categoryOfVeteran).
```

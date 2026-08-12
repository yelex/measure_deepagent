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

3. ~~**Boolean-коэрсия ловит текстовое поле**~~ (L006) — ИСПРАВЛЕНО
   2026-08-12. `startswith("category")`-эвристика заменена на явную
   карту `boolean_fields` по ЖС в `llm_extract_v2.py`.

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

### [2026-08-11] L002 LLM pipeline интеграция + первый batch-прогон
- Добавлен --llm-mode в run_pipeline_demo.py: fetch через
  agent.pipeline_mode.fetch_source, экстракция через
  revision_agent.llm_extract_v2.extract_measure_via_llm (L001-фикс),
  экспорт через существующий write_agent_export.
- Первый полный batch-прогон по всем 43 seed'ам реестра (вбд=6, сво=16,
  инвалиды=21) + score_against_golden.py.
- Результат: Recall=0.860 Precision=1.000 AvgQS=0.248 PerfectRate=0.000.
  Recall совпал числом с regex-эрой (0.86) — ожидаемо, seed-реестр тот
  же самый (LLM читает те же URL). AvgQS ниже, чем в regex-эре (была
  0.61) — генерик-промпт без тюнинга под конкретные поля хуже, чем
  hand-crafted regex. Все 7 miss_agent — ошибки *загрузки* источника
  (mos.ru ГОСТ-TLS), не экстракции.
- Замечено: почти все "category"-поля (вбд, сво, инвалиды) дают ошибку
  в scorer почти в 100% случаев — нужен разбор field_errors в L003,
  вероятно частично объясняется L006 (boolean-коэрсия ломает текстовые
  category-поля).
- Следующий шаг: L003 (сравнение field_errors LLM vs regex, тюнинг
  промпта, разбор category-полей).

### [2026-08-12] L006 boolean-fields фикс + L008 NPA search fallback

- L006 (фикс `startswith("category")` эвристики, ловившей текстовое
  `categoryOfVeteran`) был закоммичен ранее (e56ac56) явной картой
  `boolean_fields` по ЖС, но та итерация не успела прогнать batch-eval —
  backlog оставался в `todo`. Eval выполнен в этой итерации (см. ниже,
  тот же прогон) — подтверждён и закрыт как `done`.
- L008: создан `revision_agent/npa_fetcher.py::search_and_fetch_npa` —
  поиск НПА через `npa_search.search_npa` (Yandex Search API) с
  ранжированием по домену (pravo.gov.ru > cntd.ru > consultant.ru >
  garant.ru > mos.ru, PDF выше HTML) и загрузкой через собственный
  HTML-фетчер (`requests` + `apparent_encoding`, не слепой utf-8) и
  PDF-фетчер (pypdf). Интегрирован в `run_pipeline_demo.py::run_llm_mode`
  как fallback, если основной `fetch_source(url)` даёт <2000 символов.
  Побочные находки при ручном тестировании: (1) `consultant.ru/law/
  podborki/` — SEO-компиляции ссылок, не текст закона, но достаточно
  длинные, чтобы пройти порог длины — исключены явным junk-паттерном;
  (2) garant.ru/pravo.gov.ru отдают cp1251 без charset в заголовке,
  `pipeline.fetch_text` (utf-8 + `errors="ignore"`) молча съедает всю
  кириллицу — обойдено локальным `_fetch_html_text` в `npa_fetcher.py`
  (не трогая общий `fetch_text`, чтобы не задеть regex-эру).
- Batch-прогон (43 seed'а, оба изменения вместе — L006 давно в коде,
  L008 новый): fallback ни разу не потребовался (все 43 seed'а
  загрузились напрямую), но критерий успеха L008 подтверждён отдельным
  ручным тестом на `77_vbd_3` (см. backlog).
- Результат (`score_against_golden.py`, note="L008: NPA search
  fallback..."): Recall=0.860 Precision=1.000 AvgQS=0.301
  PerfectRate=0.000 (43 карточки) — Recall/Precision не изменились
  относительно L002 baseline (0.860/1.000), AvgQS вырос с 0.248 до
  0.301 (+21% относительно). По ЖС: вбд AvgQS=0.256, сво AvgQS=0.263,
  инвалиды AvgQS=0.343 (заметный рост от 0.219).
- Следующий шаг: L003 (field_errors показывают systemic terms/
  department errors во всех ЖС — вероятно проблема scorer'а без
  лемматизации, не экстрактора, см. итерацию L006 2026-08-12 00:22).
```

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

1. **Grounding mismatch** (L001): модель видит очищенный текст
   (`_strip_boilerplate` + `_cut_to_relevant`), но `_quote_grounded`
   в v2 проверял против другого представления. Нужно убедиться что
   текст идентичен в обоих местах.

2. **GLM quota**: 5-часовой лимит, 429 после исчерпания. Batch-прогоны
   нужно планировать с учётом этого.

### История экспериментов

```
### [2026-08-11] LLM-era reset
- Решение: regex метрики сброшены, backlog переписан (L001-L005)
- Baseline: пустой (0 карточек, tuning_log очищен)
- Старые метрики (regex-era): tuning_log.regex-era.jsonl.bak
```

# Ralph iteration 2026-08-12T22:34:17+02:00

## Задача
[L011] Контентный fallback-триггер для cntd.ru (ВБД пустые карточки)

## Контекст
Предыдущая итерация (коммит `051a2e2`, "ralph: L011 content trigger
(has_relevant_content) + L003 _cut_to_relevant fix with _measure_keywords")
уже внесла код для этой задачи и закоммитила его, но **не прогнала eval**
и **не обновила** `IMPROVEMENT_BACKLOG.md` (L011 всё ещё числится `todo`)
и `data/output/tuning_log.jsonl` (последняя запись — `7e687b2`, до
L011-коммита). Working tree был чистым (auto-commit сработал). Эта
итерация — не новое изменение, а завершение процесса для уже внесённого
кода: прогнать eval и закрыть L011 по факту результата (done/reverted).

## Baseline (последний валидный прогон, до L011)
tuning_log.jsonl @ 7e687b2 (2026-08-12T19:50:36, "scorer-lemma-restored"):
- Recall=0.860 Precision=1.000 AvgQS=0.371 PerfectRate=0.000 (43 карточки)
- vbd: recall=0.500 avg_qs=0.311
- сво: recall=1.000 avg_qs=0.294
- инвалиды: recall=0.955 avg_qs=0.448

## Гипотеза
L011 добавляет `has_relevant_content()` — проверяет, что текст источника
содержит содержательное упоминание темы меры (по стему ключевого слова из
названия меры, за пределами первых 3000 символов), а не только преамбулу/
рекламный мусор cntd.ru. Если контента по теме нет — триггерит НПА-поиск
через L008 fallback (`search_and_fetch_npa`), даже если текст длиннее
MIN_TEXT_LENGTH. Ожидание: часть из 6 vbd-карточек (сейчас Miss_agent for
6 из 6 при recall=0.5, т.е. 3 карточки), у которых cntd.ru отдавал
обрезанный/нерелевантный текст, теперь получат fallback на реальный текст
НПА → непустые поля → recall/AvgQS для ВБД вырастет.
Риск: fallback может сработать зря на карточках, где текст был релевантным,
но keyword просто не встретился (ложный триггер) — тогда лишний вызов
Yandex Search, не обязательно регрессия качества, но возможна деградация
latency/quota.

## План проверки
1. `python3 scripts/run_pipeline_demo.py --llm-mode`
2. `python3 scripts/score_against_golden.py --agent-export
   data/output/agent_cards_export.json --note "L011: content trigger for
   cntd.ru fallback (has_relevant_content)"`
3. Сравнить AvgQS/Recall с baseline (0.371/0.860). Не хуже → backlog L011
   → done, tuning_log уже пишется скриптом. Хуже → revert коммита 051a2e2,
   backlog L011 → reverted с описанием.

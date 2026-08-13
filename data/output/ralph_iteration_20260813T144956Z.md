# Ralph iteration 2026-08-13T14:49:56Z — Coder pass

## Задача
L011 — Контентный fallback-триггер для cntd.ru (ВБД пустые карточки), priority: critical, статус: todo.

## Baseline
Последний подтверждённый прогон (tuning_log.jsonl, commit 7e687b2,
note="scorer-lemma-restored"): Recall=0.860 Precision=1.000
AvgQS=0.371 PerfectRate=0.000 (43 карточки). По ЖС: вбд Recall=0.5
AvgQS=0.311, сво Recall=1.0 AvgQS=0.294, инвалиды Recall=0.955
AvgQS=0.448.

## Обнаружение
Код для L011 уже был написан и закоммичен в предыдущей итерации
(051a2e2 "ralph: L011 content trigger (has_relevant_content) +
L003 _cut_to_relevant fix with _measure_keywords"), до разделения
ролей coder/tester/analyst (0a1c969, 7c24cad). Тот коммит:
- `revision_agent/llm_extract_v2.py::has_relevant_content()` —
  контентный триггер: ищет стем ключевого слова названия меры
  за пределами первых 3000 символов (после преамбулы), чтобы отличить
  документ, реально содержащий текст по теме меры, от обрыва
  (cntd.ru иногда отдаёт >MIN_TEXT_LENGTH символов, но это шапка +
  мусор, без реальных статей закона).
- `scripts/run_pipeline_demo.py::run_llm_mode` — добавлена проверка
  `no_relevant_content = not too_short and not has_relevant_content(...)`,
  которая триггерит тот же fallback `search_and_fetch_npa` (L008),
  что и `too_short`.
Но eval после этого коммита не запускался, `ralph_handoff.json` не
существовал — предыдущий coder-проход не дошёл до шагов 4-5
(вероятно, был прерван до разделения ролей).

## Гипотеза
Контентный триггер поймает случаи cntd.ru, где `too_short` не
срабатывает (текст длиннее MIN_TEXT_LENGTH), но реальный контент по
теме меры отсутствует (обрыв на преамбуле + шапка/мусор) — это должно
поднять Recall/AvgQS для ЖС "вбд" (было 4/6 карточек пустые по данным
backlog L011).

## План проверки
Прогнать `run_pipeline_demo.py --llm-mode` (код уже в HEAD, изменений
вносить не нужно — задача этой итерации: доверификация уже
существующего кода через eval) + `score_against_golden.py`, сравнить
с baseline выше.

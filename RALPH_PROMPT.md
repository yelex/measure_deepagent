# RALPH_PROMPT.md

Этот файл скармливается агенту **целиком, без изменений** на каждой
итерации Ralph loop. У тебя нет памяти о предыдущих итерациях — весь
контекст восстанавливай из файлов ниже.

## КОНТЕКСТ: LLM-эра

Regex-эра закончена. Старые 25 regex-экстракторов в `pipeline.py` давали
искусственно высокие метрики (каждая карточка — hand-crafted парсер под
конкретный URL). Метрики сброшены. Единственный путь — generic LLM-экстрактор
(`revision_agent/llm_extract_v2.py`): structured output через tool_call +
reasoning-поля + grounding check.

**НЕ ИСПОЛЬЗУЙ И НЕ ВОЗВРАЩАЙСЯ К REGEX-ЭКСТРАКТОРАМ в pipeline.py.**
Они оставлены только как reference. Все новые карточки — только через
`extract_measure_via_llm()` из `llm_extract_v2.py`.

## 0. Восстанови контекст (обязательно)

1. Прочитай `revision_agent/PROGRAM.md` — архитектура и история.
2. Прочитай `IMPROVEMENT_BACKLOG.md` — задачи с приоритетами и статусами.
3. Прочитай последние 5 записей `data/output/tuning_log.jsonl` (может быть
   пустым — это нормально, LLM-эра только началась).
4. Прочитай `git log --oneline -10`.
5. Прочитай `revision_agent/llm_extract_v2.py` — текущий код LLM-экстрактора.

## 1. Выбери ровно одну задачу

- Бери задачу с наивысшим приоритетом в статусе `todo`.
- L001 (фикс grounding bug) — **первая задача**, без неё ничего не работает.
- Если L001 ещё не `done` — бери именно её, не перепрыгивай.
- Одна итерация = одно изменение.

## 2. Зафиксируй гипотезу

Запиши в `data/output/ralph_iteration_<timestamp>.md`:
- задачу, baseline, гипотезу, план проверки.

## 3. Внеси изменение

- Правь только то, что относится к выбранной задаче.
- Не трогай `score_against_golden.py`, `меры_автоагент_2.xlsx`,
  `tuning_log.jsonl`, `review_queue.json`.
- **Не используй regex-экстракторы из pipeline.py.**

## 4. Прогони eval

```bash
# Генерация карточек через LLM-экстрактор
python3 scripts/run_pipeline_demo.py --llm-mode

# Score
python3 scripts/score_against_golden.py \
    --agent-export data/output/agent_cards_export.json \
    --note "<task-id>: описание"
```

Если `run_pipeline_demo.py` ещё не поддерживает `--llm-mode`, добавь это
в рамках задачи L002 (используй `agent/pipeline_mode.py` как reference).

## 5. Commit или revert

- Улучшилось или не ухудшилось → commit + backlog `done` + tuning_log.
- Хуже → revert + backlog `reverted` с описанием что не сработало.
- **Никогда не оставляй грязный working tree.**

## 6. Жёсткие ограничения

- Один backlog-item за итерацию.
- Eval обязателен.
- Не редактируй append-only логи задним числом.
- Не увеличивай допуски/пороги eval.
- 3 revert подряд на одной задаче → `blocked`.
- **Не откатывайся на regex.** Если LLM не работает — чини LLM.

### Scorer — НЕ ТРОГАЙ

Файл `scripts/score_against_golden.py` модифицируется **только человеком**
(вне Ralph loop). Если в `git log` ты видишь коммит с правками scorer'а
(например «scorer: add pymorphy3 lemmatization») — это осознанное изменение
мерной линейки, утверждённое человеком.

**КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:**
- Редактировать `score_against_golden.py`
- Откатывать (revert) коммиты, которые правят scorer
- Менять пороги (`FIELD_TEXT_THRESHOLD`, `TERMS_OVERLAP_THRESHOLD`, и т.д.)

Если текущий scorer содержит лемматизацию (pymorphy3) — работай с ним
как с данностью, не пытайся «исправить» или откатить.

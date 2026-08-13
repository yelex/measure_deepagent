# RALPH_PROMPT_CODER.md

Этот файл скармливается агенту **целиком, без изменений** на каждой
итерации Ralph loop (Coder pass). У тебя нет памяти о предыдущих
итерациях — весь контекст восстанавливай из файлов ниже.

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

**КРИТИЧЕСКИ ВАЖНО:** Eval выполняется **синхронно**. Не запускай его
в фоне, не делай `&`, не планировал check-in — запусти и **жди
завершения процесса** в том же сеансе. Batch eval (~15 минут на 43
seed'а) — это нормально, не пытайся его ускорить или обойти.

```bash
# Генерация карточек через LLM-экстрактор (блокирует до завершения)
python3 scripts/run_pipeline_demo.py --llm-mode

# Score (блокирует до завершения)
python3 scripts/score_against_golden.py \
    --agent-export data/output/agent_cards_export.json \
    --note "<task-id>: описание"
```

Если `run_pipeline_demo.py` ещё не поддерживает `--llm-mode`, добавь это
в рамках задачи L002 (используй `agent/pipeline_mode.py` как reference).

## 5. Закоммить и передай результат Tester'у

Ты НЕ принимаешь финальное решение об успехе итерации. Твоя коммит —
черновой (`wip:`), его подтвердит или откатит независимый Tester-проход.
Не пытайся спрогнозировать его решение и не пиши в `tuning_log.jsonl`
сам — это делает Tester.

**НЕ завершай работу, пока eval полностью не закончит считать и ты не
запишешь `ralph_handoff.json`.** Если eval ещё считается — жди. Если
он упал с ошибкой — зафиксируй это в handoff с `"status": "eval_failed"`
и завершись.

После прогона eval:

1. Закоммить изменение с сообщением `wip(<task-id>): <краткое описание>`.
   Даже если результат хуже baseline — Tester сам решит revert или нет;
   Coder не имеет права молча откатывать.

2. Запиши в `data/output/ralph_handoff.json` (перезаписать файл,
   не append) JSON вида:
   ```json
   {
     "task_id": "<L0NN>",
     "commit_sha": "<git rev-parse HEAD>",
     "baseline_metrics": {"average_qs": ..., "recall": ..., "precision": ..., "perfect_rate": ...},
     "new_metrics": {"average_qs": ..., "recall": ..., "precision": ..., "perfect_rate": ...},
     "hypothesis": "<текст гипотезы>",
     "files_changed": ["<список файлов из git diff --name-only HEAD~1>"],
     "status": "awaiting_tester"
   }
   ```

3. Не трогай `IMPROVEMENT_BACKLOG.md` статус задачи — финальный статус
   (`done`/`reverted`/`blocked`) выставляет Tester, не Coder.

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

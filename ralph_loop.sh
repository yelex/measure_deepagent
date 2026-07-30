#!/usr/bin/env bash
# Ralph loop для self-improvement revision_agent.
# Каждая итерация — свежий контекст, вся преемственность через файлы
# (git, IMPROVEMENT_BACKLOG.md, tuning_log.jsonl) — см. RALPH_PROMPT.md.

set -euo pipefail

MAX_ITERATIONS="${MAX_ITERATIONS:-20}"
LOG_DIR="data/output/ralph_runs"
mkdir -p "$LOG_DIR"

for i in $(seq 1 "$MAX_ITERATIONS"); do
  ts=$(date +%Y%m%d_%H%M%S)
  echo "=== Ralph iteration $i / $MAX_ITERATIONS ($ts) ==="

  # Останавливаемся, если рабочее дерево грязное — предыдущая итерация
  # не должна была это оставлять (см. RALPH_PROMPT.md, п.6), это сигнал
  # проверить руками, а не продолжать вслепую.
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "!!! Working tree грязное перед итерацией $i — останавливаюсь, нужна ручная проверка"
    exit 1
  fi

  cat RALPH_PROMPT.md | your-agent-cli \
      > "$LOG_DIR/iteration_${i}_${ts}.log" 2>&1

  # Если backlog за N итераций подряд не даёт ни одной задачи в todo —
  # тоже стоит остановиться, а не крутиться вхолостую.
  if ! grep -q "Статус: todo" IMPROVEMENT_BACKLOG.md; then
    echo "Нет задач в статусе todo после итерации $i — пауза, нужен человек"
    exit 0
  fi

  sleep 5  # pace между итерациями — не долбить LLM/поиск без пауз
done

echo "Достигнут MAX_ITERATIONS=$MAX_ITERATIONS, останавливаюсь для ревью человеком"

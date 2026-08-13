#!/usr/bin/env bash
# Ralph loop для self-improvement revision_agent.
# Каждая итерация — свежий контекст (новый процесс claude -p), вся
# преемственность через файлы (git, IMPROVEMENT_BACKLOG.md, tuning_log.jsonl)
# — см. RALPH_PROMPT.md. Этот скрипт НЕ прогоняет eval сам — это делает
# агент внутри итерации (RALPH_PROMPT.md, п.4); скрипт только читает
# результат из data/output/tuning_log.jsonl после каждой итерации, чтобы
# решить, продолжать ли цикл.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

MAX_ITERATIONS="${MAX_ITERATIONS:-20}"        # жёсткий предохранитель
PACE_SECONDS="${PACE_SECONDS:-30}"            # пауза между итерациями
PLATEAU_WINDOW="${PLATEAU_WINDOW:-5}"         # N итераций без улучшений -> стоп
ANALYST_EVERY_N="${ANALYST_EVERY_N:-5}"        # раз в N циклов запускать Analyst
TUNING_LOG="data/output/tuning_log.jsonl"
PLATEAU_STATE="data/output/ralph_runs/plateau_state.jsonl"  # gitignored, регенерируемый
LOG_DIR="data/output/ralph_runs"
mkdir -p "$LOG_DIR"
touch "$PLATEAU_STATE"

# Цели из "Методика и критерии оценки качества извлечения данных" (§3-4):
TARGET_AVG_QS=0.90
TARGET_PERFECT_RATE=0.70
TARGET_RECALL=0.90
TARGET_PRECISION=0.85

# Читает последнюю запись tuning_log.jsonl и печатает 4 метрики через пробел
# ("None" вместо числа, если метрика ещё не определена — см. score_against_golden.py).
read_latest_metrics() {
  python3 - "$TUNING_LOG" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    if not lines:
        print("None None None None")
        sys.exit(0)
    o = json.loads(lines[-1])["overall"]
    print(o.get("average_qs"), o.get("perfect_rate"), o.get("recall"), o.get("precision"))
except Exception:
    print("None None None None")
PY
}

# Возвращает 0 (успех), если все 4 метрики достигли целей.
check_targets_met() {
  local avg_qs="$1" perfect="$2" recall="$3" precision="$4"
  python3 - "$avg_qs" "$perfect" "$recall" "$precision" \
      "$TARGET_AVG_QS" "$TARGET_PERFECT_RATE" "$TARGET_RECALL" "$TARGET_PRECISION" <<'PY'
import sys
vals = sys.argv[1:5]
targets = [float(x) for x in sys.argv[5:9]]
if any(v == "None" for v in vals):
    sys.exit(1)
vals = [float(v) for v in vals]
sys.exit(0 if all(v >= t for v, t in zip(vals, targets)) else 1)
PY
}

# Дописывает текущие метрики в PLATEAU_STATE и проверяет: улучшилась ли
# хотя бы одна метрика хоть в одной из последних PLATEAU_WINDOW записей
# относительно значения ДО этого окна. Возвращает 0 = плато (нет улучшений).
record_and_check_plateau() {
  local avg_qs="$1" perfect="$2" recall="$3" precision="$4" iter="$5"
  python3 - "$PLATEAU_STATE" "$avg_qs" "$perfect" "$recall" "$precision" "$iter" "$PLATEAU_WINDOW" <<'PY'
import json, sys
state_path, avg_qs, perfect, recall, precision, iter_, window = sys.argv[1:8]
window = int(window)

def to_num(v):
    return None if v == "None" else float(v)

entry = {"iter": int(iter_), "average_qs": to_num(avg_qs), "perfect_rate": to_num(perfect),
         "recall": to_num(recall), "precision": to_num(precision)}
with open(state_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

with open(state_path, encoding="utf-8") as f:
    history = [json.loads(l) for l in f if l.strip()]

if len(history) <= window:
    sys.exit(1)  # ещё не набралось полное окно наблюдений -> не плато

baseline = history[-(window + 1)]
recent = history[-window:]
metrics = ["average_qs", "perfect_rate", "recall", "precision"]
improved = False
for m in metrics:
    base_v = baseline.get(m)
    if base_v is None:
        continue
    for r in recent:
        rv = r.get(m)
        if rv is not None and rv > base_v:
            improved = True
            break
sys.exit(1 if improved else 0)  # 0 = плато (нет улучшений)
PY
}

append_plateau_note() {
  cat >> IMPROVEMENT_BACKLOG.md <<EOF

## [PLATEAU-$(date +%Y%m%d_%H%M%S)] Плато — нужен человек
- Статус: blocked
- Приоритет: high
- Источник: ralph_loop.sh, автоматическая детекция плато (${PLATEAU_WINDOW} итераций подряд без улучшения ни одной из 4 метрик)
- Гипотеза: —
- История: цикл остановлен автоматически, дальнейшие автономные итерации не имеют смысла без пересмотра подхода человеком
EOF
}

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

  # --- Coder pass ---
  claude -p "$(cat RALPH_PROMPT_CODER.md)" --permission-mode acceptEdits \
      > "$LOG_DIR/iteration_${i}_coder_${ts}.log" 2>&1 || {
    echo "!!! Coder завершился с ошибкой на итерации $i — смотри $LOG_DIR/iteration_${i}_coder_${ts}.log"
    exit 1
  }

  # Auto-commit after Coder pass
  git clean -fd -- data/output/tmp_ data/output/ralph_iteration_ 2>/dev/null || true
  if [[ -n "$(git status --porcelain)" ]]; then
    git add -A && git commit -m "ralph: auto-commit after coder iteration $i" 2>/dev/null || true
    echo "(auto-committed uncommitted changes after coder pass $i)"
  fi

  # --- Tester pass (обязателен сразу после Coder) ---
  claude -p "$(cat RALPH_PROMPT_TESTER.md)" --permission-mode acceptEdits \
      > "$LOG_DIR/iteration_${i}_tester_${ts}.log" 2>&1 || {
    echo "!!! Tester завершился с ошибкой на итерации $i — смотри $LOG_DIR/iteration_${i}_tester_${ts}.log"
    exit 1
  }

  # Auto-commit after Tester pass
  git clean -fd -- data/output/tmp_ data/output/ralph_iteration_ 2>/dev/null || true
  if [[ -n "$(git status --porcelain)" ]]; then
    git add -A && git commit -m "ralph: auto-commit after tester iteration $i" 2>/dev/null || true
    echo "(auto-committed uncommitted changes after tester pass $i)"
  fi

  # --- Analyst pass (раз в ANALYST_EVERY_N циклов) ---
  if (( i % ANALYST_EVERY_N == 0 )); then
    claude -p "$(cat RALPH_PROMPT_ANALYST.md)" --permission-mode acceptEdits \
        > "$LOG_DIR/iteration_${i}_analyst_${ts}.log" 2>&1 || {
      echo "!!! Analyst завершился с ошибкой на итерации $i — смотри $LOG_DIR/iteration_${i}_analyst_${ts}.log"
      exit 1
    }

    # Auto-commit after Analyst pass
    git clean -fd -- data/output/tmp_ data/output/ralph_iteration_ 2>/dev/null || true
    if [[ -n "$(git status --porcelain)" ]]; then
      git add -A && git commit -m "ralph: auto-commit after analyst iteration $i" 2>/dev/null || true
      echo "(auto-committed uncommitted changes after analyst pass $i)"
    fi
  fi

  read -r avg_qs perfect recall precision <<< "$(read_latest_metrics)"
  echo "Метрики после итерации $i: AvgQS=$avg_qs PerfectRate=$perfect Recall=$recall Precision=$precision"

  if check_targets_met "$avg_qs" "$perfect" "$recall" "$precision"; then
    echo "=== Цель достигнута: AvgQS>=$TARGET_AVG_QS, PerfectRate>=$TARGET_PERFECT_RATE, Recall>=$TARGET_RECALL, Precision>=$TARGET_PRECISION ==="
    echo "=== Финальный отчёт ==="
    tail -n1 "$TUNING_LOG" | python3 -m json.tool
    exit 0
  fi

  if record_and_check_plateau "$avg_qs" "$perfect" "$recall" "$precision" "$i"; then
    echo "!!! Плато: за последние $PLATEAU_WINDOW итераций ни одна метрика не улучшилась — останавливаюсь"
    append_plateau_note
    exit 0
  fi

  if ! grep -q "Статус: todo" IMPROVEMENT_BACKLOG.md; then
    echo "Нет задач в статусе todo после итерации $i — пауза, нужен человек"
    exit 0
  fi

  sleep "$PACE_SECONDS"  # pace между итерациями — не долбить LLM/поиск без пауз
done

echo "Достигнут MAX_ITERATIONS=$MAX_ITERATIONS, останавливаюсь для ревью человеком"

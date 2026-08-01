#!/usr/bin/env python3
"""Прогоняет seed-реестр `data/measures_registry.json` через
`revision_agent.pipeline` и пишет `data/output/agent_cards_export.json`
в формате, который читает `scripts/score_against_golden.py`.

Это демонстрационный/итерационный раннер под текущий очень узкий
pipeline (одна мера, один источник, см. revision_agent/pipeline.py) —
не финальный CLI агента (тот появится вместе с полноценным B001 на
deepagents/LLM)."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from revision_agent.export_cards import write_agent_export
from revision_agent.pipeline import run_disability_seed, run_svo_seed, run_vbd_seed

REGISTRY_PATH = REPO_ROOT / "data" / "measures_registry.json"
EXPORT_PATH = REPO_ROOT / "data" / "output" / "agent_cards_export.json"

RUNNERS = {
    "вбд": run_vbd_seed,
    "сво": run_svo_seed,
    "инвалиды": run_disability_seed,
}


def main():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards_by_ls = {"вбд": [], "сво": [], "инвалиды": []}

    for ls, runner in RUNNERS.items():
        for seed in registry.get(ls, []):
            card = runner(seed)
            cards_by_ls[ls].append(card)
            print(f"[{ls}] обработана мера: {seed['measureName']!r} <- {seed['npaUrl']}")

    write_agent_export(cards_by_ls, EXPORT_PATH)
    print(f"Экспорт записан в {EXPORT_PATH}")


if __name__ == "__main__":
    main()

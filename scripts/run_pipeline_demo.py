#!/usr/bin/env python3
"""Прогоняет seed-реестр `data/measures_registry.json` через
`revision_agent.pipeline` (regex, устарело) или через LLM-экстрактор
(`--llm-mode`, см. `revision_agent/llm_extract_v2.py`) и пишет
`data/output/agent_cards_export.json` в формате, который читает
`scripts/score_against_golden.py`.

Это демонстрационный/итерационный раннер, не финальный CLI агента."""

import argparse
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


def run_regex_mode():
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards_by_ls = {"вбд": [], "сво": [], "инвалиды": []}

    for ls, runner in RUNNERS.items():
        for seed in registry.get(ls, []):
            card = runner(seed)
            cards_by_ls[ls].append(card)
            print(f"[{ls}] обработана мера: {seed['measureName']!r} <- {seed['npaUrl']}")

    write_agent_export(cards_by_ls, EXPORT_PATH)
    print(f"Экспорт записан в {EXPORT_PATH}")


def run_llm_mode():
    """LLM-эра: fetch (agent.pipeline_mode.fetch_source) + generic
    LLM-экстрактор (llm_extract_v2.extract_measure_via_llm, L001-фикс)."""
    # Load .env (GLM_API_KEY etc.) — pipeline_mode does it on import,
    # but llm_extract_v2 reads os.environ at call time.
    import os as _os
    _env = REPO_ROOT / ".env"
    if _env.exists():
        with open(_env) as _f:
            for _line in _f:
                if "=" in _line and not _line.startswith("#"):
                    _k, _v = _line.strip().split("=", 1)
                    _os.environ.setdefault(_k, _v)

    from agent.pipeline_mode import fetch_source
    from revision_agent.llm_extract_v2 import extract_measure_via_llm
    from revision_agent.npa_fetcher import MIN_TEXT_LENGTH, search_and_fetch_npa

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards_by_ls = {"вбд": [], "сво": [], "инвалиды": []}

    for ls, seeds in registry.items():
        for seed in seeds:
            name = seed["measureName"]
            url = seed.get("npaUrl", "")
            region = seed.get("region", "Москва")
            print(f"[{ls}] {name!r} <- {url}", flush=True)

            source_text = ""
            if url:
                try:
                    source_text = fetch_source(url)
                except Exception as e:
                    print(f"    ОШИБКА загрузки: {e}", flush=True)
            else:
                print("    нет URL источника в реестре", flush=True)

            if len(source_text) < MIN_TEXT_LENGTH:
                print(f"    текст короткий ({len(source_text)} симв.) — ищу НПА через Yandex Search (L008)", flush=True)
                try:
                    source_text = search_and_fetch_npa(name, ls, region)
                    print(f"    найдено и загружено: {len(source_text)} симв.", flush=True)
                except Exception as e:
                    print(f"    ОШИБКА поиска НПА: {e}", flush=True)

            if not source_text:
                cards_by_ls[ls].append({"measureId": None, "region": region, "measureName": name})
                continue

            texts = {"источник_1": source_text[:12000]}

            if seed.get("amountsUrl"):
                try:
                    texts["источник_2_суммы"] = fetch_source(seed["amountsUrl"])[:8000]
                except Exception as e:
                    print(f"    Второй источник не загружен: {e}", flush=True)

            try:
                card = extract_measure_via_llm(seed, texts, ls, provider="glm")
            except Exception as e:
                print(f"    ОШИБКА LLM: {e}", flush=True)
                card = {"measureId": None, "region": region, "measureName": name}

            filled = sum(1 for k, v in card.items() if v is not None and k not in ("measureId", "region", "measureName"))
            total = len(card) - 3
            print(f"    {filled}/{total} полей заполнено", flush=True)
            cards_by_ls[ls].append(card)

    write_agent_export(cards_by_ls, EXPORT_PATH)
    print(f"Экспорт записан в {EXPORT_PATH}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-mode", action="store_true", help="Использовать LLM-экстрактор вместо regex")
    args = parser.parse_args()

    if args.llm_mode:
        run_llm_mode()
    else:
        run_regex_mode()


if __name__ == "__main__":
    main()

"""Pipeline-режим: детерминированный проход по мерам без agent loop.

Вместо полноценного agent loop (который делает множественные LLM-вызовы
в непредсказуемом порядке), этот модуль реализует фиксированный pipeline:

1. Загрузить текст источника (fetch_page/fetch_pdf — без LLM)
2. Извлечь карточку через LLM (extract_measure_card — один вызов GLM)
3. (Опционально) Верифицировать через второй источник

Это быстрее (1-2 LLM-вызова на меру вместо 5-10) и надёжнее для batch-прогона.
Полная agent-архитектура остаётся в main_agent.py для интерактивного режима.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

from revision_agent.pipeline import fetch_text, fetch_yandex_disk_pdf_text
from revision_agent.llm_extract import extract_via_llm
from revision_agent.models import LS_FIELDS, MeasureCard


def fetch_source(url: str, timeout: int = 20) -> str:
    """Загрузить текст источника по URL. Автоматически пробует прямой,
    через прокси, и через r.jina.ai."""
    # PDF на Яндекс.Диске
    if "disk.yandex.ru" in url or "cloud-api.yandex.net" in url:
        try:
            return fetch_yandex_disk_pdf_text(url, use_proxy=False, timeout=timeout)
        except Exception:
            return fetch_yandex_disk_pdf_text(url, use_proxy=True, timeout=timeout)

    # Обычная страница
    for strategy in [
        lambda: fetch_text(url, timeout=timeout),
        lambda: fetch_text(url, via_jina=True, timeout=timeout),
        lambda: fetch_text(url, use_proxy=True, timeout=timeout),
    ]:
        try:
            return strategy()
        except Exception:
            continue
    raise RuntimeError(f"Не удалось загрузить {url} ни одним способом")


def run_measure_pipeline(seed: dict, ls: str, verbose: bool = True) -> dict:
    """Прогон одной меры через pipeline: fetch → LLM extract.

    Args:
        seed: {"measureName": ..., "region": ..., "npaUrl": ...}
        ls: "вбд", "сво" или "инвалиды"
        verbose: печатать прогресс

    Returns:
        Карточка меры в каноническом формате
    """
    name = seed["measureName"]
    url = seed.get("npaUrl", "")
    region = seed.get("region", "Москва")

    if verbose:
        print(f"  [{ls}] {name}...", flush=True)

    # 1. Загрузить источник
    if not url:
        if verbose:
            print(f"    SKIP: нет URL источника", flush=True)
        return {"measureId": None, "region": region, "measureName": name}

    try:
        text = fetch_source(url)
        if verbose:
            print(f"    Загружено: {len(text)} симв. с {url[:60]}", flush=True)
    except Exception as e:
        if verbose:
            print(f"    ОШИБКА загрузки: {e}", flush=True)
        return {"measureId": None, "region": region, "measureName": name}

    # 2. Если есть второй URL (amountsUrl) — загрузить тоже
    texts = {"источник_1": text[:12000]}
    if seed.get("amountsUrl"):
        try:
            text2 = fetch_source(seed["amountsUrl"])
            texts["источник_2_суммы"] = text2[:8000]
            if verbose:
                print(f"    Второй источник: {len(text2)} симв.", flush=True)
        except Exception as e:
            if verbose:
                print(f"    Второй источник не загружен: {e}", flush=True)

    # 3. LLM-извлечение
    try:
        t0 = time.time()
        card = extract_via_llm(
            {"measureName": name, "region": region},
            texts,
            ls,
            provider="glm",
        )
        elapsed = time.time() - t0
        if verbose:
            filled = sum(1 for k, v in card.items() if v is not None and k not in ("measureId", "region", "measureName"))
            total = len([k for k in card if k not in ("measureId", "region", "measureName")])
            print(f"    GLM извлёк: {filled}/{total} полей за {elapsed:.1f}s", flush=True)
        return card
    except Exception as e:
        if verbose:
            print(f"    ОШИБКА LLM: {e}", flush=True)
        return {"measureId": None, "region": region, "measureName": name}


def run_batch(seeds: list[dict], ls: str, output_path: str | None = None) -> list[dict]:
    """Прогон пакета мер через pipeline.

    Args:
        seeds: список seed'ов
        ls: ЖС
        output_path: если указан, сохранить результаты в JSON

    Returns:
        Список карточек
    """
    cards = []
    print(f"\n=== Прогон {len(seeds)} мер по ЖС '{ls}' ===\n", flush=True)

    for i, seed in enumerate(seeds, 1):
        print(f"[{i}/{len(seeds)}]", flush=True)
        card = run_measure_pipeline(seed, ls)
        cards.append(card)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cards, f, ensure_ascii=False, indent=2)
        print(f"\nСохранено: {output_path} ({len(cards)} карточек)", flush=True)

    return cards


def run_all_from_registry(registry_path: str = None, output_path: str = None) -> dict:
    """Прогон всех мер из реестра по всем ЖС.

    Returns:
        {"вбд": [...], "сво": [...], "инвалиды": [...]}
    """
    if registry_path is None:
        registry_path = str(PROJECT_ROOT / "data" / "measures_registry.json")

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    all_cards = {}
    for ls, seeds in registry.items():
        if not seeds:
            continue
        cards = run_batch(seeds, ls)
        all_cards[ls] = cards

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_cards, f, ensure_ascii=False, indent=2)
        print(f"\n=== Всего: {sum(len(v) for v in all_cards.values())} карточек ===", flush=True)

    return all_cards


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pipeline актуализации мер через GLM")
    parser.add_argument("--ls", choices=["вбд", "сво", "инвалиды"], help="Только одна ЖС")
    parser.add_argument("--measure-name", help="Только одна мера по имени")
    parser.add_argument("--output", default="data/output/llm_cards_export.json")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    if args.measure_name:
        # Одна мера
        registry_path = PROJECT_ROOT / "data" / "measures_registry.json"
        with open(registry_path) as f:
            registry = json.load(f)
        for ls, seeds in registry.items():
            for s in seeds:
                if args.measure_name.lower() in s["measureName"].lower():
                    card = run_measure_pipeline(s, ls, verbose=True)
                    print(json.dumps(card, ensure_ascii=False, indent=2))
                    break
    elif args.ls:
        registry_path = PROJECT_ROOT / "data" / "measures_registry.json"
        with open(registry_path) as f:
            registry = json.load(f)
        seeds = registry.get(args.ls, [])
        run_batch(seeds, args.ls, args.output)
    else:
        run_all_from_registry(output_path=args.output)

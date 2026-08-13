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
from urllib.parse import urlparse

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
    from revision_agent.llm_extract_v2 import (
        GLMQuotaExceededError,
        extract_measure_via_llm,
        has_relevant_content,
    )
    from revision_agent.npa_fetcher import MIN_TEXT_LENGTH, search_and_fetch_npa

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    cards_by_ls = {"вбд": [], "сво": [], "инвалиды": []}
    quota_exceeded = False

    for ls, seeds in registry.items():
        if quota_exceeded:
            break
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

            too_short = len(source_text) < MIN_TEXT_LENGTH
            # L011 изначально задумывался как фикс конкретно для cntd.ru
            # (JS-рендеринг отдаёт только оголовок закона, MIN_TEXT_LENGTH
            # проходит, но текст неполный). Реализация (051a2e2) применила
            # has_relevant_content ко ВСЕМ доменам без разбора — для
            # документов, посвящённых одной конкретной мере (частый случай
            # для "сво"/"инвалиды" на mos.ru и др.), ключевое слово встречается
            # только в преамбуле (< 3000 симв.) и не повторяется дальше;
            # эвристика ошибочно бракует корректно загруженный документ и
            # форсирует fallback на Yandex Search (L008) почти для каждого
            # seed'а — это исчерпало квоту API и обрушило "инвалиды"/"сво"
            # до пустых карточек-заглушек (см. IMPROVEMENT_BACKLOG.md L011,
            # ANALYST 2026-08-13). Возвращаем триггер к изначальному объёму.
            is_cntd = "cntd.ru" in urlparse(url).netloc if url else False
            no_relevant_content = (
                is_cntd and not too_short and not has_relevant_content(source_text, name)
            )
            if too_short or no_relevant_content:
                if too_short:
                    reason = f"текст короткий ({len(source_text)} симв.)"
                else:
                    reason = "текст не содержит контента по теме меры (L011: возможен обрыв документа)"
                print(f"    {reason} — ищу НПА через Yandex Search (L008)", flush=True)
                try:
                    found_text = search_and_fetch_npa(name, ls, region)
                    print(f"    найдено и загружено: {len(found_text)} симв.", flush=True)
                    source_text = found_text
                except Exception as e:
                    print(f"    ОШИБКА поиска НПА: {e}", flush=True)

            if not source_text:
                cards_by_ls[ls].append({"measureId": None, "region": region, "measureName": name})
                continue

            # Обрезка тут — только защита от аномально больших документов;
            # реальное окно для LLM формирует _cut_to_relevant (window=8000)
            # внутри extract_measure_via_llm. Раньше [:12000] обрезали ДО
            # неё и теряли контент по мерам, чей текст в общем документе
            # (несколько мер на один npaUrl) начинается позже 12000 символов.
            texts = {"источник_1": source_text[:40000]}

            if seed.get("amountsUrl"):
                try:
                    texts["источник_2_суммы"] = fetch_source(seed["amountsUrl"])[:40000]
                except Exception as e:
                    print(f"    Второй источник не загружен: {e}", flush=True)

            try:
                card = extract_measure_via_llm(seed, texts, ls, provider="glm")
            except GLMQuotaExceededError as e:
                # L013: 5-часовое окно GLM исчерпано — retry внутри
                # _call_glm_structured уже бесполезен. Останавливаем batch
                # целиком, а не пишем карточки-заглушки для всех
                # необработанных мер (то, что молча произошло 2026-08-12 и
                # было принято за регрессию экстрактора, см.
                # IMPROVEMENT_BACKLOG.md L011/L013).
                print(f"    ОСТАНОВКА BATCH: квота GLM исчерпана — {e}", flush=True)
                quota_exceeded = True
                break
            except Exception as e:
                print(f"    ОШИБКА LLM: {e}", flush=True)
                card = {"measureId": None, "region": region, "measureName": name}

            filled = sum(1 for k, v in card.items() if v is not None and k not in ("measureId", "region", "measureName"))
            total = len(card) - 3
            print(f"    {filled}/{total} полей заполнено", flush=True)
            cards_by_ls[ls].append(card)

    write_agent_export(cards_by_ls, EXPORT_PATH)
    print(f"Экспорт записан в {EXPORT_PATH}")

    if quota_exceeded:
        n_done = sum(len(v) for v in cards_by_ls.values())
        n_total = sum(len(v) for v in registry.values())
        print(
            f"batch остановлен квотой GLM: обработано {n_done}/{n_total} карточек, "
            f"экспорт частичный — eval по нему недостоверен для необработанного остатка",
            flush=True,
        )
        sys.exit(2)


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

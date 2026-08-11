"""Tools for the deepagents harness.

Инструменты доступны агенту через create_deep_agent(tools=[...]).
Каждый инструмент — обёртка над уже существующим кодом в revision_agent/,
но в формате BaseTool для LangGraph/deepagents.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from typing import Optional

from langchain_core.tools import tool

# Импортируем существующий fetch_text и связанную логику
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from revision_agent.pipeline import fetch_text, fetch_yandex_disk_pdf_text
from revision_agent.search_searxng import search_npa as search_searxng

# Allowlist доменов (см. skills/trusted_sources/SKILL.md)
ALLOWLIST_DOMAINS = {
    "cntd.ru", "docs.cntd.ru", "consultant.ru", "garant.ru",
    "gosuslugi.ru", "sfr.gov.ru", "mos.ru", "msupport.dszn.ru",
    "dszn.ru", "sp53.mos.ru", "mosgortur.ru", "aeroexpress.ru",
    "cloud-api.yandex.net",  # API Яндекс.Диска для PDF-памяток
}


@tool
def web_search_allowlist(query: str, max_results: int = 5) -> str:
    """Поиск по вебу с фильтрацией результатов по allowlist доменов.
    Возвращает JSON со списком найденных страниц (title, url, snippet),
    только с доверенных доменов.
    """
    try:
        results = search_searxng(query, num_results=max_results * 3)
    except Exception as e:
        # SearXNG может быть недоступен (docker не запущен)
        return json.dumps({"error": f"Поиск недоступен: {e}", "results": []})

    filtered = []
    for r in results:
        url = r.get("url", r.get("link", ""))
        domain = urllib.parse.urlparse(url).hostname or ""
        # Проверяем домен и поддомены
        if any(domain == d or domain.endswith("." + d) for d in ALLOWLIST_DOMAINS):
            filtered.append({
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("content", r.get("snippet", ""))[:300],
            })
        if len(filtered) >= max_results:
            break

    return json.dumps({"results": filtered}, ensure_ascii=False)


@tool
def fetch_page(url: str, use_proxy: bool = False) -> str:
    """Скачать страницу по URL и вернуть текст без HTML-тегов.
    Для cntd.ru рекомендуется use_proxy=True (если прокси доступен).
    Для PDF-памяток на Яндекс.Диске используйте fetch_pdf.
    """
    try:
        # Автоматически используем via_jina если прямой фетч не работает
        try:
            return fetch_text(url, use_proxy=use_proxy, timeout=15)[:15000]
        except Exception:
            return fetch_text(url, via_jina=True, timeout=30)[:15000]
    except Exception as e:
        return f"Ошибка загрузки {url}: {e}"


@tool
def fetch_pdf(url: str, use_proxy: bool = False) -> str:
    """Скачать PDF с Яндекс.Диска и вернуть извлечённый текст.
    Использует публичный API Яндекс.Диска для получения прямой ссылки.
    """
    try:
        return fetch_yandex_disk_pdf_text(url, use_proxy=use_proxy)[:15000]
    except Exception as e:
        return f"Ошибка загрузки PDF {url}: {e}"


@tool
def extract_measure_card(
    measure_name: str,
    ls: str,
    source_texts: str,
    region: str = "Москва",
) -> str:
    """Извлечь карточку меры из текста источника с помощью LLM (GLM-5).

    Args:
        measure_name: Название меры (из реестра/seed)
        ls: Жизненная ситуация — "вбд", "сво" или "инвалиды"
        source_texts: Текст источника(ов), JSON: {"название": "текст"}
        region: Регион (по умолчанию Москва)

    Returns:
        JSON с карточкой меры в каноническом формате.
        Поля без подтверждения цитатой = null.
    """
    # Load .env
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v)

    from revision_agent.llm_extract_v2 import extract_measure_via_llm

    try:
        texts = json.loads(source_texts)
    except json.JSONDecodeError:
        texts = {"источник": source_texts}

    seed = {"measureName": measure_name, "region": region}
    card = extract_via_llm(seed, texts, ls, provider="glm")
    return json.dumps(card, ensure_ascii=False, indent=2)


@tool
def write_to_review_queue(finding: str) -> str:
    """Записать находку в review_queue.json (append-only).
    Используется для фиксации изменений суммы/условий меры.

    Args:
        finding: JSON с полями measure_key, field, old_value, new_value,
                 source_url, confidence (high/low)
    """
    queue_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "output", "review_queue.json"
    )

    try:
        data = json.loads(finding)
    except json.JSONDecodeError:
        return f"Ошибка: finding должен быть валидным JSON, got: {finding[:100]}"

    # Append to existing queue
    queue = []
    if os.path.exists(queue_path):
        with open(queue_path, "r", encoding="utf-8") as f:
            try:
                queue = json.load(f)
            except json.JSONDecodeError:
                queue = []

    queue.append(data)

    with open(queue_path, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    return f"Записано в review_queue.json (всего записей: {len(queue)})"

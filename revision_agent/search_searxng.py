"""Полнотекстовый поиск НПА через самостоятельно поднятый SearXNG —
резервный/основной способ поиска, не завязанный на платный API.

Использовался как fallback в родственном проекте
(`auto/revision_agent/tools.py`, `auto/scripts/experiments/searxng_npa_spike/`),
здесь — как основной способ, потому что Yandex Search API (см.
`npa_search.py`) на имеющемся ключе отдаёт `403 Permission denied` (см.
`IMPROVEMENT_BACKLOG.md` B004) — это проблема IAM/биллинга на стороне
пользователя, не решается кодом; SearXNG не требует ключа вообще.

Поднимается через `infra/searxng/docker-compose.yml` (тот же
`searxng-settings/settings.yml`, что и в `auto`, порт 8082 — 8081 занят
на этой машине другим процессом):

    cd infra/searxng && docker compose up -d
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Dict, List

SEARXNG_URL = "http://localhost:8082"

TRUSTED_DOMAINS = [
    "docs.cntd.ru",
    "consultant.ru",
    "garant.ru",
    "gosuslugi.ru",
    "sfr.gov.ru",
    "mos.ru",
    "pravo.gov.ru",
]


def search_npa(query: str, max_results: int = 8, restrict_domains: bool = True) -> List[Dict]:
    """Ищет через локальный SearXNG. Поднят ли контейнер — не проверяется
    молча: сетевая ошибка при обращении к SEARXNG_URL всплывает как есть."""
    site_filter = " OR ".join(f"site:{d}" for d in TRUSTED_DOMAINS) if restrict_domains else ""
    full_query = f"({site_filter}) {query}" if site_filter else query

    params = urllib.parse.urlencode({"q": full_query, "format": "json"})
    req = urllib.request.Request(f"{SEARXNG_URL}/search?{params}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in data.get("results", [])[:max_results]
    ]

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

Порт был захардкожен без возможности переопределения — минимум три
итерации (77_11, 77_12, 77_19, см. `IMPROVEMENT_BACKLOG.md` B004/B003)
констатировали "SearXNG недоступен, docker daemon не поднят" на основании
одной проверки этого порта, хотя в iteration_20260811_204736 `docker ps`
показал реально работающий контейнер SearXNG на порту 8888 (не наш
`searxng_measure_deepagent` из `docker-compose.yml` этого репозитория —
похоже, отдельный, уже поднятый на этом хосте инстанс; результаты поиска
через него проверены и релевантны). Порт теперь читается из
`SEARXNG_URL` env var (тот же паттерн, что `RU_PROXY_URL` в
`pipeline.py`) — дефолт не меняется, но следующая итерация, если найдёт
SearXNG на другом порту, может выставить `SEARXNG_URL=http://localhost:8888`
явно, а не заново решать "docker не поднят".
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Dict, List

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8082")

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

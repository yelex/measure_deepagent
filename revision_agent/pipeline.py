"""Минимальный, узкоспециализированный pipeline проверки одной меры.

Это НЕ общая ReAct-архитектура из AGENTS.md (search_subagent +
verification_subagent на deepagents/LLM) — та требует LLM_PROVIDER,
который пока TBD (см. AGENTS.md, TODO). Это первый честный сквозной
срез: реальный HTTP-фетч реального источника + эвристическое (без LLM)
извлечение полей для ОДНОЙ конкретной меры на ОДНОМ конкретном сайте.
Не претендует на обобщение — следующая итерация должна расширять набор
источников/эвристик, а не переписывать это с нуля.

Важное найденное ограничение инфраструктуры: `cntd.ru` и `mos.ru`
(allowlist из AGENTS.md) недоступны из текущей песочницы
(`ECONNREFUSED`), `gosuslugi.ru` отдаёт anti-bot страницу. Обычный
`requests`/`curl` без браузерного User-Agent получает 403 даже там, где
хост доступен (проверено на aeroexpress.ru) — отсюда обязательный
заголовок ниже. Это открытый вопрос для реального
поискового/fetch-инструмента агента (см. AGENTS.md TODO
"поисковый инструмент"), не только для этого скрипта.
"""

from __future__ import annotations

import re
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_text(url: str, timeout: int = 15) -> str:
    """Скачивает страницу и возвращает текст без HTML-тегов."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_vbd_aeroexpress_card(seed: dict, page_text: str) -> dict:
    """Эвристика для ОДНОЙ конкретной страницы (aeroexpress.ru/aero/info/benefits.html).

    Специально узкая (одна мера, один источник) — это первый слой
    пайплайна, не универсальный экстрактор. department извлекается
    поиском предложения "Оформить льготный билет можно в ..."; terms и
    sum сознательно не извлекаются (страница не даёт единого текста
    условий/суммы под конкретно вбд-категорию без более сложного
    парсинга по спискам категорий — см. field_errors после прогона
    scorer'а для приоритизации следующей итерации). categoryOfVeteran
    тоже не угадывается: страница ссылается на "пп. 1-4 п. 1 ст. 3 ФЗ
    «О ветеранах»", а соответствия этой формулировки категориальной
    метке эталона (например "Военнослужащие") в проекте пока нет
    справочника — угадывать вместо честного None было бы имитацией
    результата, а не извлечением.
    """
    department = None
    m = re.search(r"[Оо]формить льготный билет можно ([^.]+)\.", page_text)
    if m:
        department = "Оформить льготный билет можно " + m.group(1).strip()

    return {
        "measureId": None,
        "region": seed["region"],
        "categoryOfVeteran": None,
        "measureName": seed["measureName"],
        "measureSum": None,
        "measurePeriodicity": None,
        "measureTerms": None,
        "department": department,
    }


def run_vbd_seed(seed: dict) -> dict:
    """Прогоняет одну вбд-мера из реестра через фетч + извлечение."""
    if seed["npaUrl"] == "https://aeroexpress.ru/aero/info/benefits.html":
        page_text = fetch_text(seed["npaUrl"])
        return extract_vbd_aeroexpress_card(seed, page_text)
    raise NotImplementedError(
        f"Нет эвристики извлечения для источника {seed['npaUrl']!r} — "
        "добавь новую в отдельной ralph-итерации, не угадывай молча."
    )

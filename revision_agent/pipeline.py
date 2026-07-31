"""Минимальный, узкоспециализированный pipeline проверки одной меры.

Это НЕ общая ReAct-архитектура из AGENTS.md (search_subagent +
verification_subagent на deepagents/LLM) — та требует LLM_PROVIDER,
который пока TBD (см. AGENTS.md, TODO). Это первый честный сквозной
срез: реальный HTTP-фетч реального источника + эвристическое (без LLM)
извлечение полей для ОДНОЙ конкретной меры на ОДНОМ конкретном сайте.
Не претендует на обобщение — следующая итерация должна расширять набор
источников/эвристик, а не переписывать это с нуля.

## Сетевой доступ к allowlist-источникам (см. AGENTS.md)

Прямой (без прокси) фетч из текущей песочницы: `cntd.ru`/`mos.ru` —
`ECONNREFUSED`, `gosuslugi.ru` — anti-bot страница, `aeroexpress.ru` —
работает (нужен браузерный User-Agent, иначе 403).

Через российский прокси пользователя (используется в родственных
проектах `auto/scripts/search_yandex.py`,
`social-support-agent/src/proxy_downloader.py`; см. `RU_PROXY_URL`
ниже) при прогоне 2026-07-31:
- `cntd.ru` — **работает**. Сайт делает SSO-редирект через
  `auth.kodeks.ru` (анонимная сессия, логин не нужен) — обязателен
  cookie jar на протяжении редиректов, без него бесконечный редирект-луп.
  Подтверждено: реально получен текст Закона города Москвы №33
  "О транспортном налоге" (документ 3691928).
- `mos.ru` — TCP-туннель через прокси устанавливается, но TLS-хендшейк
  падает (`SSL_ERROR_SYSCALL` после `Client Key Exchange`/`Finished`,
  воспроизводится и с `--insecure`) — похоже на требование ГОСТ-шифров,
  которые не поддерживает стандартный LibreSSL/OpenSSL-клиент без
  спецсборки (например `gost-engine`). Не решено — см.
  `IMPROVEMENT_BACKLOG.md`.
- `gosuslugi.ru` — не отвечает и через прокси (таймаут). Не решено.

Так что реально пригодный для этого пайплайна источник НПА-текста на
сегодня — `cntd.ru` (через прокси) и любой сайт вроде `aeroexpress.ru`,
отвечающий напрямую.
"""

from __future__ import annotations

import http.cookiejar
import os
import re
import urllib.request

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Российский прокси пользователя для доступа к cntd.ru/mos.ru и т.п. из
# площадок без прямого доступа к рунету (тот же прокси, что и в
# родственных проектах `auto`, `social-support-agent`). Переопределяется
# через переменную окружения — не считать этот адрес секретом, но и не
# считать его гарантированно постоянным/бесплатным ресурсом.
RU_PROXY_URL = os.environ.get("RU_PROXY_URL", "http://95.142.42.28:8888")


def _unescape_js_unicode(text: str) -> str:
    """cntd.ru встраивает копию контента как JSON с \\uXXXX-эскейпами
    (для client-side hydration) поверх обычного HTML — без этого шага
    в тексте остаются буквальные "\\u003c" и т.п."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def fetch_text(url: str, timeout: int = 15, use_proxy: bool = False) -> str:
    """Скачивает страницу и возвращает текст без HTML-тегов.

    use_proxy=True — через RU_PROXY_URL с cookie jar (нужно для
    источников с SSO-редиректом вроде cntd.ru, см. docstring модуля).
    """
    if use_proxy:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": RU_PROXY_URL, "https": RU_PROXY_URL}),
            urllib.request.HTTPCookieProcessor(cj),
        )
        opener.addheaders = [("User-Agent", USER_AGENT)]
        with opener.open(url, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    else:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
    text = _unescape_js_unicode(text)
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

"""Поиск НПА по названию меры через Yandex Search API + загрузка полного
текста найденного документа — фоллбэк для случаев, когда `npaUrl` из
seed-реестра либо неизвестен (регионы кроме Москвы), либо отдаёт только
оголовок документа без JS-рендеринга (cntd.ru), см. IMPROVEMENT_BACKLOG.md
L008.
"""

from __future__ import annotations

import io
import re
import urllib.request

import requests
from pypdf import PdfReader

from revision_agent.npa_search import search_npa
from revision_agent.pipeline import USER_AGENT, fetch_text

# Приоритет доменов при ранжировании результатов поиска — официальные
# публикаторы (pravo.gov.ru) и кодекс (cntd.ru) точнее агрегаторов
# (consultant.ru/garant.ru), mos.ru — последний из-за известной
# ГОСТ-TLS проблемы (см. revision_agent/pipeline.py docstring).
DOMAIN_PRIORITY = ["pravo.gov.ru", "cntd.ru", "consultant.ru", "garant.ru", "mos.ru"]

MIN_TEXT_LENGTH = 2000

# consultant.ru "подборки" — SEO-компиляции ссылок/тизеров подписки, не
# текст закона; выглядят достаточно длинными, чтобы пройти
# MIN_TEXT_LENGTH, но не содержат норм права. Обнаружено вручную при
# тестировании L008: подборка забивала реальный текст закона с garant.ru,
# который шёл следом по рангу.
JUNK_URL_PATTERNS = ["consultant.ru/law/podborki/"]


def _is_junk(url: str) -> bool:
    return any(p in url for p in JUNK_URL_PATTERNS)


def _domain_rank(url: str) -> int:
    for i, domain in enumerate(DOMAIN_PRIORITY):
        if domain in url:
            return i
    return len(DOMAIN_PRIORITY)


def _rank_results(results: list[dict]) -> list[dict]:
    return sorted(results, key=lambda r: (_domain_rank(r["url"]), not r["url"].lower().endswith(".pdf")))


def _fetch_pdf_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        pdf_bytes = resp.read()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n---PAGE---\n".join(pages)
    return re.sub(r"[ \t]+", " ", text).strip()


def _fetch_html_text(url: str, timeout: int = 15) -> str:
    """Как `pipeline.fetch_text`, но с определением кодировки по байтам
    (`apparent_encoding`), а не слепым utf-8. garant.ru/pravo.gov.ru не
    указывают charset в заголовках и отдают cp1251 — `fetch_text`
    (`errors="ignore"` поверх utf-8-декода) молча съедает всю кириллицу,
    оставляя нечитаемый мусор из цифр/пунктуации. Обнаружено вручную при
    тестировании L008."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    html = resp.text
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&laquo;", "«").replace("&raquo;", "»")
    return re.sub(r"\s+", " ", text).strip()


def _fetch_result_text(url: str) -> str:
    if url.lower().endswith(".pdf"):
        return _fetch_pdf_text(url)
    for strategy in [
        lambda: _fetch_html_text(url),
        lambda: fetch_text(url, via_jina=True),
        lambda: fetch_text(url, use_proxy=True),
    ]:
        try:
            return strategy()
        except Exception:
            continue
    raise RuntimeError(f"Не удалось загрузить {url} ни одним способом")


def search_and_fetch_npa(measure_name: str, ls: str, region: str = "Москва", max_results: int = 8) -> str:
    """Найти НПА по названию меры через Yandex Search API и вернуть
    полный текст лучшего загружаемого результата.

    Перебирает результаты в порядке приоритета домена (см.
    DOMAIN_PRIORITY, PDF — приоритетнее HTML того же домена) и
    возвращает текст первого результата, чья загрузка дала не меньше
    MIN_TEXT_LENGTH символов. Бросает RuntimeError, если ни один
    результат не подошёл.
    """
    query = f"{measure_name} {region} льгота закон"
    results = search_npa(query, max_results=max_results)
    if not results:
        raise RuntimeError(f"Yandex Search не нашёл НПА для {measure_name!r} ({region}, {ls})")

    errors = []
    for r in _rank_results(results):
        url = r["url"]
        if _is_junk(url):
            errors.append(f"{url}: известный junk-паттерн, пропущен")
            continue
        try:
            text = _fetch_result_text(url)
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue
        if len(text) < MIN_TEXT_LENGTH:
            errors.append(f"{url}: слишком короткий текст ({len(text)} симв.)")
            continue
        return text

    raise RuntimeError(
        f"Не удалось загрузить полный текст НПА для {measure_name!r} ни из одного "
        f"результата поиска: {'; '.join(errors)}"
    )

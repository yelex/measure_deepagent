"""Полнотекстовый поиск НПА по доверенным доменам через Yandex Search API v2.

Нужен, когда уже известная ссылка на НПА (из реестра/эталона) ведёт
только на базовый закон, а конкретная норма (например льгота) вынесена
в отдельный, не процитированный документ — см. `Обработка НПА.docx`
п.3.1 и `IMPROVEMENT_BACKLOG.md` B004.

Ключ и folder id берутся из переменных окружения
`YANDEX_SEARCH_API_KEY`/`YANDEX_SEARCH_FOLDER_ID` — **не хардкодить в
этом файле**: то же самое API уже используется в родственном проекте
(`auto/scripts/search_yandex.py`, тот же биллинговый Yandex Cloud folder
пользователя), но с оплатой на его аккаунте — переменные окружения не
дают секрету попасть в git этого репозитория.
"""

from __future__ import annotations

import base64
import os
import xml.etree.ElementTree as ET
from typing import List, Dict

import urllib.request
import json

TRUSTED_DOMAINS = [
    "cntd.ru",
    "consultant.ru",
    "garant.ru",
    "gosuslugi.ru",
    "sfr.gov.ru",
    "mos.ru",
    "pravo.gov.ru",
]

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"


def _build_query(query: str) -> str:
    site_filter = " | ".join(f"site:{d}" for d in TRUSTED_DOMAINS)
    return f"({site_filter}) {query}"


def search_npa(query: str, max_results: int = 5) -> List[Dict]:
    api_key = os.environ.get("YANDEX_SEARCH_API_KEY")
    folder_id = os.environ.get("YANDEX_SEARCH_FOLDER_ID")
    if not api_key or not folder_id:
        raise RuntimeError(
            "YANDEX_SEARCH_API_KEY / YANDEX_SEARCH_FOLDER_ID не заданы в окружении — "
            "поиск не выполняется молча без них."
        )

    body = json.dumps({
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": _build_query(query)},
        "folderId": folder_id,
        "responseFormat": "FORMAT_XML",
        "resultsCount": max_results,
    }).encode("utf-8")

    req = urllib.request.Request(
        SEARCH_URL, data=body, method="POST",
        headers={"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    raw_data = data.get("rawData", "")
    if not raw_data:
        return []
    xml_text = base64.b64decode(raw_data).decode("utf-8")
    return _parse_xml_results(xml_text)


def _parse_xml_results(xml_text: str) -> List[Dict]:
    results = []
    root = ET.fromstring(xml_text)
    for group in root.findall(".//group"):
        doc = group.find("doc")
        if doc is None:
            continue
        title_el = doc.find("title")
        url_el = doc.find("url")
        snippet_el = doc.find("headline")
        if snippet_el is None:
            snippet_el = doc.find("passages/passage")
        results.append({
            "title": "".join(title_el.itertext()).strip() if title_el is not None else "",
            "url": url_el.text.strip() if url_el is not None else "",
            "snippet": "".join(snippet_el.itertext()).strip() if snippet_el is not None else "",
        })
    return results

"""Main deepagents harness — полная архитектура из AGENTS.md.

 create_deep_agent с:
- model: GLM-5 через LangChain ChatOpenAI (OpenAI-compatible API z.ai)
- tools: web_search_allowlist, fetch_page, fetch_pdf, extract_measure_card, write_to_review_queue
- subagents: search_subagent (поиск источников), verification_subagent (верификация с двойным подтверждением)
- skills: measure_extraction, trusted_sources, citation_verification
- interrupt_on: write_to_review_queue (human-in-the-loop на high-impact находки)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Load .env
PROJECT_ROOT = Path(__file__).parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent, SubAgent
from agent.tools import (
    web_search_allowlist,
    fetch_page,
    fetch_pdf,
    extract_measure_card,
    write_to_review_queue,
)


def _load_skill(path: str) -> str:
    """Загружает SKILL.md как текст для системного промпта."""
    full = PROJECT_ROOT / path
    if full.exists():
        return full.read_text(encoding="utf-8")
    return ""


def build_agent():
    """Создаёт и возвращает deepagents harness.

    Использование:
        agent = build_agent()
        result = agent.invoke({
            "messages": [{"role": "user", "content": "Актуализируй меру 77_svo_1"}]
        })
    """
    # GLM-5 через OpenAI-compatible API
    model = ChatOpenAI(
        model="glm-4.7-flash",
        api_key=os.environ["GLM_API_KEY"],
        base_url=os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4"),
        temperature=0,
        max_tokens=4000,
    )

    # Системный промпт из скилов
    system_prompt = """Ты — агент актуализации мер социальной поддержки в Москве.
Твоя задача: проверять актуальность мер для трёх ЖС: инвалиды, СВО, ВБД.

## Твой цикл работы (на каждую меру):

1. **Поиск**: найди актуальный текст источника по названию меры.
   Используй web_search_allowlist для поиска по доверенным доменам.
   Загрузи текст через fetch_page или fetch_pdf.

2. **Извлечение**: вызови extract_measure_card с текстом источника.
   Модель извлечёт поля карточки с обязательным цитированием.

3. **Верификация**: для изменений суммы или условий — найди второй
   независимый источник (двойное подтверждение). Если источник один —
   пометь finding как low_confidence.

4. **Запись**: если найдено расхождение с текущими данными —
   вызови write_to_review_queue с описанием находки.

## Правила:
- Не придумывай данные. Если поле не подтверждено цитатой — оно null.
- Один источник на сумму/условие = low_confidence.
- Цитирование URL обязательно на каждую находку.
- Разделяй по ЖС: инвалиды / СВО / ВБД — не смешивать категории.

## Доверенные домены:
cntd.ru, consultant.ru, garant.ru, gosuslugi.ru, sfr.gov.ru,
mos.ru, dszn.ru, msupport.dszn.ru, mosgortur.ru

## Структуры полей по ЖС — см. skills/measure_extraction/SKILL.md
"""

    # Добавляем текст скилов
    for skill_path in [
        "skills/measure_extraction/SKILL.md",
        "skills/trusted_sources/SKILL.md",
        "skills/citation_verification/SKILL.md",
    ]:
        skill_text = _load_skill(skill_path)
        if skill_text:
            system_prompt += f"\n\n--- {skill_path} ---\n{skill_text}"

    # Subagents
    search_subagent = SubAgent(
        name="search_subagent",
        description="Поиск источников по allowlist доменам. Использует web_search_allowlist и fetch_page/fetch_pdf для сбора текстов НПА и официальных страниц.",
        system_prompt="""Ты — поисковый субагент. Твоя задача: найти релевантные источники для меры.
1. Сформулируй поисковый запрос по названию меры.
2. Вызови web_search_allowlist.
3. Загрузи тексты найденных страниц через fetch_page или fetch_pdf.
4. Верни JSON: {"sources": [{"name": ..., "url": ..., "text": ...}]}.
Не выдумывай источники — только то, что реально найдено.""",
        tools=[web_search_allowlist, fetch_page, fetch_pdf],
    )

    verification_subagent = SubAgent(
        name="verification_subagent",
        description="Верификация извлечённых данных. Сверяет поля карточки с текстом источника, требует двойного подтверждения для high-impact находок.",
        system_prompt="""Ты — верификационный субагент. Твоя задача: проверить извлечённую карточку меры.
1. Для каждого поля проверь, подтверждается ли оно цитатой из источника.
2. Для суммы и условий — найди второй независимый источник.
3. Если поле подтверждено только одним источником — пометь low_confidence.
4. Верни JSON: {"verified_fields": [...], "low_confidence_fields": [...], "second_source_found": true/false}.
""",
        tools=[web_search_allowlist, fetch_page, fetch_pdf],
    )

    # Создаём агента
    agent = create_deep_agent(
        model=model,
        tools=[
            web_search_allowlist,
            fetch_page,
            fetch_pdf,
            extract_measure_card,
            write_to_review_queue,
        ],
        system_prompt=system_prompt,
        subagents=[search_subagent, verification_subagent],
        skills=[
            "skills/measure_extraction",
            "skills/trusted_sources",
            "skills/citation_verification",
        ],
        interrupt_on={
            "write_to_review_queue": True,  # human-in-the-loop
        },
    )

    return agent


def run_measure(measure_seed: dict, ls: str) -> dict:
    """Прогон одной меры через агента.

    Args:
        measure_seed: {"measureName": ..., "region": ..., "npaUrl": ...}
        ls: "вбд", "сво" или "инвалиды"

    Returns:
        dict с результатом агента (messages из последнего состояния)
    """
    agent = build_agent()
    prompt = (
        f"Актуализируй меру: {measure_seed['measureName']} "
        f"(регион: {measure_seed.get('region', 'Москва')}, ЖС: {ls}). "
        f"Ссылка на НПА: {measure_seed.get('npaUrl', 'не указана')}. "
        f"Найди актуальный текст, извлеки карточку, проверь double confirmation "
        f"и запиши находки в review_queue если есть расхождения."
    )
    result = agent.invoke({
        "messages": [{"role": "user", "content": prompt}]
    })
    return result


if __name__ == "__main__":
    # CLI: python -m agent.main_agent
    import argparse
    parser = argparse.ArgumentParser(description="Agent актуализации мер")
    parser.add_argument("--measure-name", required=True, help="Название меры")
    parser.add_argument("--ls", required=True, choices=["вбд", "сво", "инвалиды"])
    parser.add_argument("--region", default="Москва")
    parser.add_argument("--npa-url", default="")
    args = parser.parse_args()

    result = run_measure(
        {"measureName": args.measure_name, "region": args.region, "npaUrl": args.npa_url},
        args.ls,
    )

    # Print last message
    messages = result.get("messages", [])
    if messages:
        last = messages[-1]
        content = last.content if hasattr(last, "content") else str(last)
        print(content)

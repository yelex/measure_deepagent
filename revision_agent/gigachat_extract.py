"""GigaChat-провайдер для LLM-экстракции карточки меры.

Заменяет _call_glm_structured (raw HTTP к z.ai) на GigaChat через
langchain_gigachat + bind_tools. Сохраняет ту же Pydantic-схему
(_build_extraction_schema) и SYSTEM_PROMPT из llm_extract_v2.py.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional, Type

from langchain_gigachat import GigaChat
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel


def _create_gigachat_model() -> GigaChat:
    """Создаёт GigaChat-модель из переменных окружения."""
    return GigaChat(
        credentials=os.environ["GIGACHAT_CREDENTIALS"],
        model=os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Max"),
        base_url=os.environ.get(
            "GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1"
        ),
        auth_url=os.environ.get(
            "GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        ),
        scope=os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_CORP"),
        verify_ssl_certs=os.environ.get("GIGACHAT_VERIFY_SSL_CERTS", "False").lower()
        in ("true", "1", "yes"),
        profanity_check=os.environ.get("GIGACHAT_PROFANITY_CHECK", "False").lower()
        in ("true", "1", "yes"),
        temperature=0,
        timeout=int(os.environ.get("GIGACHAT_TIMEOUT", "120")),
        max_tokens=int(os.environ.get("GIGACHAT_MAX_TOKENS", "8000")),
    )


class GigaChatQuotaExceededError(RuntimeError):
    """GigaChat квота исчерпана (аналог GLMQuotaExceededError)."""


def call_gigachat_structured(
    system_prompt: str,
    user_prompt: str,
    schema_model: Type[BaseModel],
    retries: int = 2,
) -> dict:
    """Вызов GigaChat с forced tool_call (structured output).

    Возвращает распарсенный dict с полями карточки.

    Args:
        system_prompt: Системный промпт (SYSTEM_PROMPT из llm_extract_v2).
        user_prompt: Пользовательский промпт с текстом источника.
        schema_model: Pydantic-модель схемы (из _build_extraction_schema).
        retries: Количество ретраев при временных ошибках.

    Returns:
        Dict с полями карточки (распарсенный tool_call arguments).
    """
    model = _create_gigachat_model()
    bound = model.bind_tools([schema_model])

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = bound.invoke(messages)

            # Проверяем tool_calls
            tool_calls = getattr(resp, "tool_calls", None) or []
            if tool_calls:
                args = tool_calls[0]["args"]
                # args уже dict (langchain парсит JSON автоматически)
                if isinstance(args, str):
                    args = json.loads(args)
                return args

            # Нет tool_call — проверяем content
            content = getattr(resp, "content", "")
            if content and content.strip():
                # Иногда модель возвращает JSON в content вместо tool_call
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass

            last_err = RuntimeError(
                f"GigaChat вернул пустой ответ без tool_call "
                f"(content={content[:200]!r})"
            )
        except Exception as e:
            err_str = str(e)
            # Квота / rate limit
            if "429" in err_str or "quota" in err_str.lower():
                raise GigaChatQuotaExceededError(err_str) from e
            last_err = RuntimeError(f"GigaChat error: {err_str}")

        if attempt < retries:
            time.sleep(3)

    raise last_err

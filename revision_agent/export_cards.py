"""Сериализация карточек агента в формат, ожидаемый
`scripts/score_against_golden.py --agent-export` (см. docstring того
модуля — три ключа вбд/сво/инвалиды, список карточек с каноническими
именами полей)."""

from __future__ import annotations

import json
from pathlib import Path

from revision_agent.models import LS_FIELDS


def write_agent_export(cards_by_ls: dict, path: Path) -> None:
    out = {}
    for ls in LS_FIELDS:
        out[ls] = cards_by_ls.get(ls, [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

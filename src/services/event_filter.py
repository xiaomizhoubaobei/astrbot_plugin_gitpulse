"""Event subscription whitelist parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from astrbot.api import logger

from ..core.constants import SUPPORTED_EVENT_TYPES

# 非 JSON 数组时按逗号/空白/换行切分，兼容 "push, issues" 这种写法。
_SPLIT_PATTERN = re.compile(r"[,\s]+")


def parse_enabled_events(raw: Any) -> frozenset[str] | None:
    """解析 ``enabled_events`` 配置.

    Returns:
        未配置（None/空白）时返回 ``None``，表示启用全部支持的事件；
        配置为 JSON 数组或逗号/空白分隔字符串时返回规范化的事件集合。
        配置非法时返回空集合（禁用全部可订阅事件）并记录告警，
        避免拼写错误被当作「未配置」而意外放开全部事件。
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                data: Any = json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning(f"GitHub Webhook: Invalid enabled_events JSON: {exc}")
                return frozenset()
        else:
            data = _SPLIT_PATTERN.split(text)
    elif isinstance(raw, (list, tuple, set, frozenset)):
        data = list(raw)
    else:
        logger.warning("GitHub Webhook: enabled_events must be a string or list")
        return frozenset()

    if not isinstance(data, (list, tuple, set, frozenset)):
        logger.warning("GitHub Webhook: enabled_events must be a JSON array")
        return frozenset()

    events: set[str] = set()
    for item in data:
        if not isinstance(item, str):
            logger.warning(f"GitHub Webhook: Ignoring non-string event name {item!r}")
            continue
        event = item.strip().lower()
        if not event:
            continue
        if event not in SUPPORTED_EVENT_TYPES:
            logger.warning(
                f"GitHub Webhook: Unknown event type {event!r} in enabled_events"
            )
        events.add(event)

    return frozenset(events)

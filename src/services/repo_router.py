"""Repository to target UMO routing for multi-repo notifications."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

# 允许的键：owner/repo、owner/* 或全局 *；拒绝空白与多余层级。
_REPO_KEY_PATTERN = re.compile(r"^(?:\*|[^/\s]+/(?:\*|[^/\s]+))$")


def _normalize_targets(value: Any) -> list[str] | None:
    """把单个 UMO 或 UMO 列表规整为去空、去重的列表；非法值返回 None."""
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        return None

    targets: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            return None
        target = item.strip()
        if not target:
            return None
        if target not in targets:
            targets.append(target)
    return targets


def parse_repo_routes(raw: Any) -> dict[str, list[str]]:
    """解析 ``repo_routes`` 配置，返回规整后的仓库到 UMO 列表映射.

    配置可以是 JSON 字符串（WebUI 的 text 类型）或已解析的映射。
    仓库键统一转小写，GitHub 仓库名大小写不敏感；非法条目跳过并告警。
    """
    if raw is None:
        return {}

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(f"GitHub Webhook: Invalid repo_routes JSON: {exc}")
            return {}
    elif isinstance(raw, Mapping):
        data = raw
    else:
        logger.warning("GitHub Webhook: repo_routes must be a JSON object string")
        return {}

    if not isinstance(data, Mapping):
        logger.warning("GitHub Webhook: repo_routes must be a JSON object")
        return {}

    routes: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            logger.warning(f"GitHub Webhook: repo_routes key {key!r} is not a string")
            continue

        repo_key = key.strip().lower()
        if not _REPO_KEY_PATTERN.fullmatch(repo_key):
            logger.warning(
                f"GitHub Webhook: Invalid repo_routes key {key!r}, "
                f"expected owner/repo, owner/* or *"
            )
            continue

        targets = _normalize_targets(value)
        if targets is None:
            logger.warning(
                f"GitHub Webhook: Invalid repo_routes value for {key!r}, "
                f"expected UMO string or list of UMO strings"
            )
            continue

        routes[repo_key] = targets

    return routes


def resolve_target_umos(
    repo_full_name: str,
    routes: Mapping[str, list[str]],
    fallback: str,
) -> list[str]:
    """按 精确仓库 → owner/* → * → target_umo 的顺序解析发送目标.

    命中空列表表示显式静音该仓库，不再回落到 ``target_umo``。
    """
    key = (repo_full_name or "").strip().lower()
    owner = key.split("/", 1)[0] if "/" in key else ""

    for candidate in (key, f"{owner}/*" if owner else "", "*"):
        if candidate and candidate in routes:
            return list(routes[candidate])

    return [fallback] if fallback else []


def get_repo_full_name(payload: Mapping[str, Any]) -> str:
    """从 GitHub payload 提取 repository.full_name，缺失时返回空串."""
    repository = payload.get("repository")
    if isinstance(repository, Mapping):
        full_name = repository.get("full_name")
        if isinstance(full_name, str):
            return full_name.strip()
    return ""

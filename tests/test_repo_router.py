"""Tests for repository to target UMO routing."""

import pytest

from src.services.repo_router import (
    get_repo_full_name,
    parse_repo_routes,
    resolve_target_umos,
)

_ROUTES = {
    "owner/repo_a": ["umo:a1", "umo:a2"],
    "owner/*": ["umo:owner"],
    "*": ["umo:global"],
    "muted/repo": [],
}


def test_parse_empty_config_returns_no_routes():
    """未配置或空白文本时不产生任何路由."""
    assert parse_repo_routes(None) == {}
    assert parse_repo_routes("") == {}
    assert parse_repo_routes("   ") == {}


def test_parse_invalid_input_returns_no_routes():
    """非法 JSON、非对象 JSON 或错误类型都回落空映射."""
    assert parse_repo_routes("{not json") == {}
    assert parse_repo_routes("[]") == {}
    assert parse_repo_routes(123) == {}


def test_parse_normalizes_keys_and_targets():
    """键统一小写，目标去空白去重，单字符串自动包成列表."""
    raw = """
    {
      " Owner/Repo_A ": [
        " platform:GroupMessage:1 ",
        "platform:GroupMessage:2",
        "platform:GroupMessage:1"
      ],
      "owner/*": "platform:GroupMessage:3",
      "owner/repo_b": []
    }
    """
    assert parse_repo_routes(raw) == {
        "owner/repo_a": ["platform:GroupMessage:1", "platform:GroupMessage:2"],
        "owner/*": ["platform:GroupMessage:3"],
        "owner/repo_b": [],
    }


def test_parse_accepts_mapping_input():
    """宿主直接提供映射时无需再解析 JSON."""
    assert parse_repo_routes({"owner/repo": ["umo:1"]}) == {"owner/repo": ["umo:1"]}


@pytest.mark.parametrize(
    "raw",
    [
        '{"owner/repo": 123}',
        '{"owner/repo": ["ok", 123]}',
        '{"owner/repo": ["  "]}',
        '{"owner/repo": null}',
        '{"owner/repo/sub": ["umo:1"]}',
        '{"bad key": ["umo:1"]}',
    ],
)
def test_parse_skips_invalid_entries(raw):
    """非法键或非法目标值的条目应被跳过."""
    assert parse_repo_routes(raw) == {}


def test_parse_keeps_valid_entries_when_others_invalid():
    """单条非法不影响其它合法路由."""
    raw = '{"owner/repo": ["umo:1"], "bad key": ["umo:2"], "owner/other": 42}'
    assert parse_repo_routes(raw) == {"owner/repo": ["umo:1"]}


def test_resolve_exact_match_preferred():
    """精确仓库优先，且匹配不区分大小写."""
    assert resolve_target_umos("Owner/Repo_A", _ROUTES, "fallback") == [
        "umo:a1",
        "umo:a2",
    ]


def test_resolve_owner_wildcard():
    """未知仓库回落到 owner/*."""
    assert resolve_target_umos("owner/unknown", _ROUTES, "fallback") == ["umo:owner"]


def test_resolve_global_wildcard():
    """其它组织回落到全局 *."""
    assert resolve_target_umos("other/repo", _ROUTES, "fallback") == ["umo:global"]


def test_resolve_fallback_when_no_match():
    """没有任何路由命中时使用 target_umo 兜底."""
    assert resolve_target_umos("other/repo", {}, "fallback") == ["fallback"]


def test_resolve_empty_mapping_mutes_repo():
    """命中空数组表示显式静音，不再回落到 target_umo."""
    assert resolve_target_umos("muted/repo", _ROUTES, "fallback") == []


def test_resolve_without_fallback_returns_empty():
    """兜底目标为空且无路由命中时不发送消息."""
    assert resolve_target_umos("owner/repo", {}, "") == []


def test_get_repo_full_name_strips_whitespace():
    """提取 repository.full_name 并去除首尾空白."""
    payload = {"repository": {"full_name": " owner/repo "}}
    assert get_repo_full_name(payload) == "owner/repo"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"repository": None},
        {"repository": {}},
        {"repository": {"full_name": 1}},
    ],
)
def test_get_repo_full_name_missing_returns_empty(payload):
    """仓库字段缺失或类型错误时返回空串，交由兜底逻辑处理."""
    assert get_repo_full_name(payload) == ""

"""Tests for the enabled_events subscription whitelist."""

import pytest

from src.services.event_filter import parse_enabled_events


def test_unset_or_blank_enables_all_events():
    """未配置或空白表示启用全部支持的事件."""
    assert parse_enabled_events(None) is None
    assert parse_enabled_events("") is None
    assert parse_enabled_events("   ") is None


def test_comma_separated_string():
    """逗号/空白分隔的字符串应解析为集合."""
    assert parse_enabled_events("push, issues\npull_request") == frozenset(
        {"push", "issues", "pull_request"}
    )


def test_json_array_string():
    """JSON 数组写法同样支持."""
    assert parse_enabled_events('["push", "release"]') == frozenset(
        {"push", "release"}
    )


def test_list_input():
    """宿主直接提供列表时也能解析."""
    assert parse_enabled_events(["push", "star"]) == frozenset({"push", "star"})


def test_names_are_normalized_and_deduplicated():
    """事件名统一小写、去空白、去重."""
    assert parse_enabled_events(" Push , PUSH , release ") == frozenset(
        {"push", "release"}
    )


def test_invalid_json_disables_all_events():
    """非法 JSON 不应被当作未配置，避免意外放开全部事件."""
    assert parse_enabled_events('["push"') == frozenset()


def test_invalid_type_disables_all_events():
    """非字符串/列表类型视为非法配置."""
    assert parse_enabled_events(123) == frozenset()
    assert parse_enabled_events({"push": True}) == frozenset()


def test_non_string_items_are_skipped():
    """数组中非字符串元素被跳过，其余有效项保留."""
    assert parse_enabled_events(["push", 1, None]) == frozenset({"push"})


@pytest.mark.parametrize("event", ["deployment", "fork", "typo_event"])
def test_unknown_event_names_are_kept(event):
    """未知事件名保留在集合中（不会命中），并记录告警."""
    assert parse_enabled_events(event) == frozenset({event})

"""Tests for pull request event formatter."""

import pytest

from src.core.constants import (
    ACTION_PR_CLOSED,
    ACTION_PR_OPENED,
    ACTION_PR_REOPENED,
    ACTION_PR_SYNCHRONIZE,
)
from src.formatters.pull_request_formatter import (
    ACTION_EMOJI,
    DEFAULT_ACTION_EMOJI,
    format_pull_request_message,
)


def _format(action: str) -> str:
    """按固定字段渲染一条 PR 消息，聚焦动作相关的输出。"""
    return format_pull_request_message(
        action=action,
        author_name="alice",
        repo_name="owner/repo",
        pr_number=7,
        title="feat: 示例 PR",
        base_branch="main",
        head_branch="feature/x",
        pr_url="https://example.com/pull/7",
    )


@pytest.mark.parametrize(
    ("action", "emoji"),
    [
        (ACTION_PR_OPENED, "🆕"),
        (ACTION_PR_CLOSED, "✅"),
        (ACTION_PR_REOPENED, "🔄"),
        (ACTION_PR_SYNCHRONIZE, "🔀"),
    ],
)
def test_known_actions_render_expected_emoji(action: str, emoji: str):
    """已知 PR 动作应命中各自的 emoji，而非回落默认 📝。"""
    message = _format(action)
    assert message.startswith(emoji)
    assert f"alice {action} PR in owner/repo" in message


def test_synchronize_action_uses_merge_emoji():
    """回归 issue #4：GitHub 发送的 synchronize 必须命中 🔀。"""
    message = _format("synchronize")
    assert message.startswith("🔀")
    assert not message.startswith(DEFAULT_ACTION_EMOJI)


def test_unknown_action_falls_back_to_default_emoji():
    """未覆盖的动作回落默认 emoji，不抛异常。"""
    message = _format("labeled")
    assert message.startswith(DEFAULT_ACTION_EMOJI)


def test_action_constant_matches_github_payload_enum():
    """常量值须与 GitHub webhook 的 activity type 完全一致（无尾字母 d）.

    GitHub 的 PR 同步动作是美式拼写 ``synchronize``；写成 ``synchronized``
    会永远匹配不上真实 payload。
    """
    assert ACTION_PR_SYNCHRONIZE == "synchronize"
    # 映射键集合锁定为 GitHub 真实动作，防止再次混入拼写变体
    assert set(ACTION_EMOJI) == {"opened", "closed", "reopened", "synchronize"}

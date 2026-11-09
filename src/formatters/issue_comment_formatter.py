"""Issue / PR comment event formatter."""

from ..core.constants import (
    ACTION_COMMENT_CREATED,
    ACTION_COMMENT_DELETED,
    ACTION_COMMENT_EDITED,
)

# 单条评论摘要最大字符数，避免长评论撑爆群消息。
COMMENT_BODY_LIMIT = 100

ACTION_EMOJI = {
    ACTION_COMMENT_CREATED: "💬",
    ACTION_COMMENT_EDITED: "📝",
    ACTION_COMMENT_DELETED: "🗑️",
}

DEFAULT_ACTION_EMOJI = "💬"

ACTION_VERB = {
    ACTION_COMMENT_CREATED: "commented on",
    ACTION_COMMENT_EDITED: "edited a comment on",
    ACTION_COMMENT_DELETED: "deleted a comment on",
}


def _short_body(body: str) -> str:
    """把评论压成单行摘要：取首行、压缩空白、超长截断."""
    text = (body or "").strip()
    first_line = text.splitlines()[0] if text else ""
    collapsed = " ".join(first_line.split())
    if len(collapsed) > COMMENT_BODY_LIMIT:
        collapsed = collapsed[: COMMENT_BODY_LIMIT - 1] + "…"
    return collapsed


def format_issue_comment_message(
    action: str,
    author_name: str,
    repo_name: str,
    issue_number: int,
    title: str,
    is_pull_request: bool,
    comment_body: str,
    comment_url: str,
) -> str:
    """Format issue_comment event message (also used for PR comments)."""
    action_emoji = ACTION_EMOJI.get(action, DEFAULT_ACTION_EMOJI)
    verb = ACTION_VERB.get(action, f"{action} a comment on")
    label = "PR" if is_pull_request else "Issue"

    lines = [
        f"{action_emoji} GitHub Issue Comment Event",
        f"👤 {author_name} {verb} {label.lower()} in {repo_name}",
        f"📋 {label} #{issue_number}: {title}",
    ]

    body = _short_body(comment_body)
    if body:
        # 用 💭 区分评论正文与动作 emoji（created 的 💬），避免消息行歧义。
        lines.append(f"💭 {body}")
    if comment_url:
        lines.append(f"📎 {comment_url}")
    return "\n".join(lines)

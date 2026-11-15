"""Tests for the in-chat GitHub command service（REST 调用与人话错误映射）."""

import pytest

import src.services.github_command_service as service
from src.formatters.github_command_formatter import (
    format_command_failure,
    format_command_success,
)


class _FakeResponse:
    """最小 aiohttp 响应桩：可配置状态码与响应体."""

    def __init__(self, status: int = 200, text: str = ""):
        self.status = status
        self._text = text

    async def __aenter__(self):
        """支持 `async with session.request(...) as response`."""
        return self

    async def __aexit__(self, *exc_info):
        """退出上下文时不吞异常."""
        return False

    async def text(self):
        """返回预设响应体."""
        return self._text


def _patch_session(monkeypatch, response=None, error=None):
    """替换 aiohttp.ClientSession，并记录请求方法 / URL / 请求头 / 载荷."""
    captured = {}

    class _FakeSession:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def request(self, method, url, headers=None, json=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["json"] = json
            if error:
                raise error
            return response

    monkeypatch.setattr(service.aiohttp, "ClientSession", _FakeSession)
    return captured


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "default_repo", "expected"),
    [
        ("12", "owner/repo", ("owner/repo", 12)),
        ("#12", "owner/repo", ("owner/repo", 12)),
        ("owner/other#7", "owner/repo", ("owner/other", 7)),
        ("owner/other #7", "owner/repo", ("owner/other", 7)),
    ],
)
def test_parse_target_accepts_supported_forms(raw, default_repo, expected):
    """编号写法：裸数字 / 带 # / 带仓库前缀，均应正确解析."""
    assert service.parse_target(raw, default_repo) == expected


@pytest.mark.parametrize(
    ("raw", "default_repo"),
    [("", "owner/repo"), ("abc", "owner/repo"), ("#0", "owner/repo"), ("12", "")],
)
def test_parse_target_rejects_invalid_input(raw, default_repo):
    """空值、非数字、零编号、缺默认仓库都应抛 CommandError."""
    with pytest.raises(service.CommandError):
        service.parse_target(raw, default_repo)


def test_parse_repo_whitelist_supports_csv_and_json():
    """白名单兼容逗号分隔与 JSON 数组两种写法，并统一小写."""
    assert service.parse_repo_whitelist("Owner/A, owner/b") == frozenset(
        {"owner/a", "owner/b"}
    )
    assert service.parse_repo_whitelist('["Owner/A"]') == frozenset({"owner/a"})
    assert service.parse_repo_whitelist(None) is None
    assert service.parse_repo_whitelist("   ") is None


def test_parse_repo_whitelist_skips_invalid_entries():
    """非法条目（缺 owner、含穿越路径段）跳过，其余照常保留."""
    repos = service.parse_repo_whitelist(["owner/a", "b", "owner/../c", 5])
    assert repos == frozenset({"owner/a"})


def test_parse_repo_whitelist_handles_bad_json_and_wrong_type():
    """JSON 写错或类型不对时都收敛为「全部拒绝」，不抛异常."""
    assert service.parse_repo_whitelist("[owner/repo") == frozenset()
    assert service.parse_repo_whitelist(123) == frozenset()


def test_ensure_repo_allowed_refuses_when_whitelist_missing():
    """未配置白名单时必须拒绝写操作，不能「漏配即放开」."""
    with pytest.raises(service.CommandError):
        service.ensure_repo_allowed("owner/repo", None)
    with pytest.raises(service.CommandError):
        service.ensure_repo_allowed("owner/repo", frozenset())


def test_ensure_repo_allowed_checks_membership_case_insensitively():
    """白名单命中不区分大小写，未命中则拒绝."""
    service.ensure_repo_allowed("Owner/Repo", frozenset({"owner/repo"}))
    with pytest.raises(service.CommandError):
        service.ensure_repo_allowed("owner/other", frozenset({"owner/repo"}))


@pytest.mark.parametrize("raw", ["squash", "MERGE", " rebase "])
def test_normalize_merge_method_accepts_supported(raw):
    """合并方式合法值归一化为小写."""
    assert service.normalize_merge_method(raw) in {"squash", "merge", "rebase"}


def test_normalize_merge_method_falls_back_to_squash_for_invalid_default():
    """配置里给了非法默认值时按 squash 处理，不因配置失误阻断指令."""
    assert service.normalize_merge_method("  ") == "squash"


def test_normalize_merge_method_rejects_unknown():
    """未知合并方式直接拒绝，默认值走 squash."""
    assert service.normalize_merge_method("") == "squash"
    with pytest.raises(service.CommandError):
        service.normalize_merge_method("fast-forward")


def test_normalize_comment_body_checks_empty_and_length():
    """评论正文非空且不超上限."""
    assert service.normalize_comment_body("  hi  ") == "hi"
    with pytest.raises(service.CommandError):
        service.normalize_comment_body("   ")
    with pytest.raises(service.CommandError):
        service.normalize_comment_body("x" * (service.MAX_COMMAND_COMMENT_CHARS + 1))


# --------------------------------------------------------------------------
# API 调用
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_pull_request_success(monkeypatch):
    """合并成功时带上合并方式与短 SHA，且请求落在自家 API 地址."""
    captured = _patch_session(
        monkeypatch, _FakeResponse(200, '{"sha": "abcdef0123456789", "merged": true}')
    )

    outcome = await service.merge_pull_request("owner/repo", 12, "t0ken", "squash")

    assert outcome.ok is True
    assert "squash" in outcome.detail
    assert "abcdef0" in outcome.detail
    assert outcome.url == service.GITHUB_WEB_BASE + "/owner/repo/pull/12"
    assert captured["method"] == "PUT"
    assert (
        captured["url"] == service.GITHUB_API_BASE + "/repos/owner/repo/pulls/12/merge"
    )
    assert captured["json"] == {"merge_method": "squash"}
    assert captured["headers"]["Authorization"] == "Bearer t0ken"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expect"),
    [
        (401, "Token"),
        (403, "权限"),
        (404, "找不到"),
        (405, "无法合并"),
        (409, "head 分支"),
        (500, "服务异常"),
    ],
)
async def test_merge_pull_request_maps_status_to_chinese(monkeypatch, status, expect):
    """各类失败状态码都翻译成可直接回显的中文原因."""
    _patch_session(monkeypatch, _FakeResponse(status, '{"message": "boom"}'))

    outcome = await service.merge_pull_request("owner/repo", 12, "t0ken")

    assert outcome.ok is False
    assert expect in outcome.error


@pytest.mark.asyncio
async def test_merge_pull_request_handles_network_error(monkeypatch):
    """网络异常时不抛栈，返回可回显的原因."""
    _patch_session(monkeypatch, error=RuntimeError("connection reset"))

    outcome = await service.merge_pull_request("owner/repo", 12, "t0ken")

    assert outcome.ok is False
    assert "网络" in outcome.error


@pytest.mark.asyncio
async def test_set_issue_state_close_marks_pull_request(monkeypatch):
    """关闭 PR 时按 payload 中的 pull_request 字段标注目标类型."""
    captured = _patch_session(
        monkeypatch,
        _FakeResponse(
            200,
            '{"html_url": "https://github.com/owner/repo/pull/12", "pull_request": {}}',
        ),
    )

    outcome = await service.set_issue_state("owner/repo", 12, "t0ken", close=True)

    assert outcome.ok is True
    assert "PR" in outcome.detail
    assert outcome.url == "https://github.com/owner/repo/pull/12"
    assert captured["method"] == "PATCH"
    assert captured["json"] == {"state": "closed"}


@pytest.mark.asyncio
async def test_set_issue_state_reopen_labels_issue(monkeypatch):
    """重新打开普通 Issue 时标注为 Issue，状态值为 open."""
    captured = _patch_session(
        monkeypatch, _FakeResponse(200, '{"html_url": "https://x/1", "number": 1}')
    )

    outcome = await service.set_issue_state("owner/repo", 1, "t0ken", close=False)

    assert outcome.ok is True
    assert "Issue" in outcome.detail
    assert captured["json"] == {"state": "open"}


@pytest.mark.asyncio
async def test_create_issue_comment_posts_body(monkeypatch):
    """评论接口使用 POST，并把正文原样送到 GitHub."""
    captured = _patch_session(
        monkeypatch,
        _FakeResponse(201, '{"html_url": "https://github.com/owner/repo/issues/9#c1"}'),
    )

    outcome = await service.create_issue_comment("owner/repo", 9, "t0ken", "你好")

    assert outcome.ok is True
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/repos/owner/repo/issues/9/comments")
    assert captured["json"] == {"body": "你好"}


@pytest.mark.asyncio
async def test_request_survives_non_json_body(monkeypatch):
    """响应非 JSON 时只告警，不抛异常，交由调用方回显人话."""
    _patch_session(monkeypatch, _FakeResponse(502, "<html>bad gateway</html>"))

    outcome = await service.set_issue_state("owner/repo", 3, "t0ken", close=True)

    assert outcome.ok is False
    assert "5xx" in outcome.error or "502" in outcome.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expect"),
    [
        ("merge", "合并"),
        ("comment", "评论"),
        ("close", "Issue"),
    ],
)
async def test_422_hint_depends_on_action(monkeypatch, action, expect):
    """422 的中文原因按动作区分，不用一句笼统文案误导群成员."""
    _patch_session(monkeypatch, _FakeResponse(422, '{"message": "invalid"}'))

    if action == "merge":
        outcome = await service.merge_pull_request("owner/repo", 12, "t0ken")
    elif action == "comment":
        outcome = await service.create_issue_comment("owner/repo", 12, "t0ken", "hi")
    else:
        outcome = await service.set_issue_state("owner/repo", 12, "t0ken", close=True)

    assert outcome.ok is False
    assert expect in outcome.error


# --------------------------------------------------------------------------
# 回显格式化
# --------------------------------------------------------------------------


def test_format_command_success_contains_repo_number_and_url():
    """成功回显包含动作、仓库#编号与链接."""
    text = format_command_success(
        action="merge",
        repo="owner/repo",
        number=12,
        detail="合并方式：squash",
        url="https://github.com/owner/repo/pull/12",
    )

    assert "🔀" in text
    assert "owner/repo#12" in text
    assert "squash" in text
    assert "https://github.com/owner/repo/pull/12" in text


def test_format_command_success_omits_empty_lines():
    """无详情、无链接时不输出空行."""
    text = format_command_success(action="close", repo="owner/repo", number=3)
    assert len(text.splitlines()) == 1


def test_format_command_failure_prefixes_warning():
    """失败回显统一带 ⚠️ 前缀，便于群成员一眼识别."""
    assert format_command_failure("没权限").startswith("⚠️")


@pytest.mark.asyncio
async def test_request_returns_status_when_body_empty(monkeypatch):
    """204/200 空响应体时不尝试解析 JSON，直接回状态码."""
    _patch_session(monkeypatch, _FakeResponse(405, ""))

    outcome = await service.merge_pull_request("owner/repo", 12, "t0ken")

    assert outcome.ok is False
    assert "无法合并" in outcome.error


@pytest.mark.asyncio
async def test_invalid_repo_is_rejected_before_any_request(monkeypatch):
    """非法仓库名（路径穿越）在拼路径时就被拒，网络层完全不会被调用."""
    captured = _patch_session(monkeypatch, _FakeResponse(200, "{}"))

    with pytest.raises(service.CommandError):
        await service.set_issue_state("owner/../x", 1, "t0ken", close=True)

    assert "url" not in captured


@pytest.mark.asyncio
async def test_set_issue_state_handles_network_error(monkeypatch):
    """状态变更的网络异常同样转成人话."""
    _patch_session(monkeypatch, error=TimeoutError("timed out"))

    outcome = await service.set_issue_state("owner/repo", 1, "t0ken", close=True)

    assert outcome.ok is False
    assert "网络" in outcome.error


@pytest.mark.asyncio
async def test_create_issue_comment_maps_forbidden(monkeypatch):
    """评论接口 403 时提示权限/限流原因."""
    _patch_session(monkeypatch, _FakeResponse(403, '{"message": "forbidden"}'))

    outcome = await service.create_issue_comment("owner/repo", 9, "t0ken", "hi")

    assert outcome.ok is False
    assert "403" not in outcome.error
    assert "权限" in outcome.error


@pytest.mark.asyncio
async def test_request_maps_unlisted_status(monkeypatch):
    """未在映射表内的状态码回落到通用话术，仍带状态码便于排查."""
    _patch_session(monkeypatch, _FakeResponse(418, '{"message": "teapot"}'))

    outcome = await service.create_issue_comment("owner/repo", 9, "t0ken", "hi")

    assert "418" in outcome.error


# --------------------------------------------------------------------------
# 指令编排层
# --------------------------------------------------------------------------


class _Cfg:
    """最小 PluginConfig 桩：只暴露编排层用到的属性."""

    def __init__(self, **overrides):
        self.github_token = "t0ken"
        self.command_repos = frozenset({"owner/repo"})
        self.github_command_default_repo = "owner/repo"
        self.github_command_merge_method = "squash"
        for key, value in overrides.items():
            setattr(self, key, value)


@pytest.mark.asyncio
async def test_run_command_help_for_empty_target():
    """目标为空时回显缺目标提示，不发任何请求."""
    from src.commands.github_command import run_github_command

    result = await run_github_command("close", _Cfg(), "")

    assert result.startswith("⚠️")
    assert "编号" in result


@pytest.mark.asyncio
async def test_run_command_rejects_unsupported_action():
    """未知动作（如 pull）回显不支持，不会误发请求."""
    from src.commands.github_command import run_github_command

    result = await run_github_command("pull", _Cfg(), "12")

    assert "不支持的操作" in result


@pytest.mark.asyncio
async def test_run_command_comment_requires_body():
    """评论缺少正文时提示补全，不发请求."""
    from src.commands.github_command import run_github_command

    result = await run_github_command("comment", _Cfg(), "12", "")

    assert "正文不能为空" in result


@pytest.mark.asyncio
async def test_run_command_reopen_calls_state_service(monkeypatch):
    """reopen 走状态变更分支，close=False."""
    import src.commands.github_command as module

    captured = {}

    async def _fake_state(repo, number, token, *, close):
        captured.update(repo=repo, number=number, close=close)
        return module.CommandOutcome(ok=True, detail="目标类型：Issue")

    monkeypatch.setattr(module, "set_issue_state", _fake_state)

    result = await module.run_github_command("reopen", _Cfg(), "#3")

    assert "owner/repo#3" in result
    assert captured["close"] is False


@pytest.mark.asyncio
async def test_run_command_comment_passes_body_verbatim(monkeypatch):
    """评论正文原样透传（含空格），不被重新切分."""
    import src.commands.github_command as module

    captured = {}

    async def _fake_comment(repo, number, token, body):
        captured.update(body=body)
        return module.CommandOutcome(ok=True, detail="ok")

    monkeypatch.setattr(module, "create_issue_comment", _fake_comment)

    await module.run_github_command("comment", _Cfg(), "9", "已修复 这个问题")

    assert captured["body"] == "已修复 这个问题"


@pytest.mark.asyncio
async def test_run_command_renders_service_failure(monkeypatch):
    """服务层返回失败时直接回显其中文原因，不包装成成功格式."""
    import src.commands.github_command as module

    async def _fake_merge(repo, number, token, method):
        return module.CommandOutcome(ok=False, error="该 PR 当前无法合并")

    monkeypatch.setattr(module, "merge_pull_request", _fake_merge)

    result = await module.run_github_command("merge", _Cfg(), "12")

    assert result.startswith("⚠️")
    assert "无法合并" in result


def test_parse_command_line_empty_text():
    """空消息不产生动作，handler 会据此回帮助."""
    from src.commands.github_command import parse_command_line

    assert parse_command_line("") == ("", "", "")


@pytest.mark.asyncio
async def test_create_issue_comment_handles_network_error(monkeypatch):
    """评论接口的网络异常也转成人话，不抛栈."""
    _patch_session(monkeypatch, error=RuntimeError("connection reset"))

    outcome = await service.create_issue_comment("owner/repo", 9, "t0ken", "hi")

    assert outcome.ok is False
    assert "网络" in outcome.error


def test_ensure_repo_allowed_rejects_invalid_repo_name():
    """仓库名本身非法（缺 owner 或路径穿越）即使白名单命中也要拒绝."""
    with pytest.raises(service.CommandError):
        service.ensure_repo_allowed("../x", frozenset({"../x"}))
    with pytest.raises(service.CommandError):
        service.ensure_repo_allowed("noslash", frozenset({"noslash"}))

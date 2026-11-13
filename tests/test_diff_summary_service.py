"""Tests for the optional AI diff summary service."""

import asyncio
from types import SimpleNamespace

import pytest

import src.services.diff_summary_service as service
from src.core.constants import MAX_DIFF_RESPONSE_BYTES


class _FakeContent:
    """最小 aiohttp 响应内容桩，支持按字节上限读取."""

    def __init__(self, payload: bytes):
        self.payload = payload

    async def read(self, n: int = -1) -> bytes:
        """返回预设内容；n >= 0 时模拟 aiohttp 的按字节截断."""
        return self.payload if n < 0 else self.payload[:n]


class _FakeResponse:
    """最小 aiohttp 响应桩，可配置状态码."""

    def __init__(self, payload: bytes = b"", status: int = 200):
        self.status = status
        self.content = _FakeContent(payload)

    async def __aenter__(self):
        """支持 `async with session.get(...) as response`."""
        return self

    async def __aexit__(self, *exc_info):
        """退出上下文时不吞异常."""
        return False


def _patch_session(monkeypatch, payload: bytes = b"", status: int = 200, error=None):
    """替换 aiohttp.ClientSession，并记录请求 URL 与请求头."""
    captured = {}

    class _FakeSession:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            if error:
                raise error
            return _FakeResponse(payload, status)

    monkeypatch.setattr(service.aiohttp, "ClientSession", _FakeSession)
    return captured


class _FakeContext:
    """最小 LLM Context 桩，按需返回 provider 或生成结果."""

    def __init__(
        self,
        provider_id="provider-1",
        provider_error=None,
        response=None,
        generate_error=None,
        delay=0,
    ):
        self.provider_id = provider_id
        self.provider_error = provider_error
        self.response = response
        self.generate_error = generate_error
        self.delay = delay
        self.llm_calls = []

    async def get_current_chat_provider_id(self, umo):
        """返回默认 provider，或模拟查询失败."""
        if self.provider_error:
            raise self.provider_error
        return self.provider_id

    async def llm_generate(self, **kwargs):
        """记录调用参数，返回预设响应或模拟失败/超时."""
        self.llm_calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.generate_error:
            raise self.generate_error
        return self.response


class _FakePlugin:
    """最小插件桩：只暴露摘要服务需要的 cfg 与 context."""

    def __init__(self, context, **cfg_overrides):
        self.context = context
        cfg = {
            "llm_provider_id": "provider-1",
            "agent_timeout": 60,
            "github_token": "",
            "code_review_max_diff_chars": 12000,
            "target_umo": "test:GroupMessage:1",
        }
        cfg.update(cfg_overrides)
        self.cfg = SimpleNamespace(**cfg)


def _response(text):
    """构造带 completion_text 的 LLM 响应桩."""
    return SimpleNamespace(completion_text=text)


def _push_payload(**overrides):
    """构造最小 push payload."""
    payload = {
        "repository": {"full_name": "owner/repo"},
        "ref": "refs/heads/main",
        "before": "a" * 40,
        "after": "b" * 40,
        "commits": [
            {"message": "feat: add thing\n\nbody"},
            {"message": "fix: handle edge case"},
        ],
    }
    payload.update(overrides)
    return payload


def _pr_payload(action="opened", **overrides):
    """构造最小 pull_request payload."""
    payload = {
        "action": action,
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 12,
            "title": "Add feature",
            "base": {"ref": "main"},
            "head": {"ref": "feature"},
        },
    }
    payload.update(overrides)
    return payload


def test_build_diff_url_for_push_compare():
    """普通 push 应使用 before...after 的 compare diff 地址."""
    assert (
        service.build_diff_url("push", _push_payload())
        == f"https://api.github.com/repos/owner/repo/compare/{'a' * 40}...{'b' * 40}"
    )


def test_build_diff_url_for_new_branch_uses_head_commit():
    """before 为全 0（新分支）时退回单个 head 提交的 diff 地址."""
    payload = _push_payload(before="0" * 40)

    assert (
        service.build_diff_url("push", payload)
        == "https://api.github.com/repos/owner/repo/commits/" + "b" * 40
    )


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_build_diff_url_for_reviewable_pr_actions(action):
    """新建、重开、新增提交的 PR 都应生成 PR diff 地址."""
    assert (
        service.build_diff_url("pull_request", _pr_payload(action))
        == "https://api.github.com/repos/owner/repo/pulls/12"
    )


@pytest.mark.parametrize("action", ["closed", "labeled", "edited", None])
def test_build_diff_url_skips_non_reviewable_pr_actions(action):
    """关闭/打标签等不产生新代码的动作不触发摘要."""
    assert service.build_diff_url("pull_request", _pr_payload(action)) is None


def test_build_diff_url_skips_push_without_commits():
    """分支/Tag 生命周期事件没有 commits，不触发摘要."""
    assert service.build_diff_url("push", _push_payload(commits=[])) is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "repository": {"full_name": "owner/repo;rm"},
            "commits": [{}],
            "after": "b" * 40,
        },
        {"repository": {"full_name": "../evil"}, "commits": [{}], "after": "b" * 40},
        {"repository": {}, "commits": [{}], "after": "b" * 40},
        {
            "repository": {"full_name": "owner/repo"},
            "commits": [{}],
            "after": "not-a-sha",
        },
    ],
)
def test_build_diff_url_rejects_invalid_fields(payload):
    """非法仓库名或 SHA 必须放弃摘要，避免拼出任意请求地址."""
    assert service.build_diff_url("push", payload) is None


def test_build_diff_url_skips_unsupported_event():
    """非 push / pull_request 事件不产生摘要."""
    assert service.build_diff_url("issues", _pr_payload()) is None


@pytest.mark.asyncio
async def test_fetch_diff_sends_github_headers(monkeypatch):
    """请求应带 Accept/User-Agent；配置 token 时附加 Bearer 鉴权."""
    captured = _patch_session(monkeypatch, b"diff --git a/x b/x")

    result = await service.fetch_diff("https://api.github.com/x", token="secret")

    assert result == "diff --git a/x b/x"
    assert captured["headers"]["Accept"] == "application/vnd.github.v3.diff"
    assert captured["headers"]["User-Agent"] == "GitPulse-AstrBot-Plugin"
    assert captured["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_fetch_diff_omits_authorization_without_token(monkeypatch):
    """未配置 token 时不得发送 Authorization 头."""
    captured = _patch_session(monkeypatch, b"diff")

    await service.fetch_diff("https://api.github.com/x")

    assert "Authorization" not in captured["headers"]


@pytest.mark.asyncio
async def test_fetch_diff_returns_empty_on_http_error(monkeypatch):
    """GitHub 返回非 200 时返回空串，由调用方跳过摘要."""
    _patch_session(monkeypatch, b"nope", status=404)

    assert await service.fetch_diff("https://api.github.com/x") == ""


@pytest.mark.asyncio
async def test_fetch_diff_returns_empty_on_exception(monkeypatch):
    """网络异常不得抛出，返回空串保持主流程稳定."""
    _patch_session(monkeypatch, error=RuntimeError("network down"))

    assert await service.fetch_diff("https://api.github.com/x") == ""


@pytest.mark.asyncio
async def test_fetch_diff_truncates_oversized_body(monkeypatch):
    """响应体超过字节上限时只保留前 N 字节."""
    _patch_session(monkeypatch, b"x" * (MAX_DIFF_RESPONSE_BYTES + 100))

    result = await service.fetch_diff("https://api.github.com/x")

    assert len(result) == MAX_DIFF_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_summarize_changes_returns_cleaned_summary(monkeypatch):
    """正常链路应清理空行并返回 LLM 摘要."""

    async def fake_fetch(url, token="", timeout=0):
        return "diff --git a/x b/x\n+new line"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(response=_response("  - 新增功能\n\n  - 修复边界  \n"))
    plugin = _FakePlugin(context)

    summary = await service.summarize_changes(plugin, "push", _push_payload())

    assert summary == "- 新增功能\n- 修复边界"
    assert context.llm_calls[0]["chat_provider_id"] == "provider-1"
    assert context.llm_calls[0]["system_prompt"] == service.CODE_REVIEW_SYSTEM_PROMPT
    assert "diff --git" in context.llm_calls[0]["prompt"]
    assert "fix: handle edge case" in context.llm_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_summarize_changes_skips_when_diff_empty(monkeypatch):
    """拉不到 diff 时不调用 LLM."""

    async def fake_fetch(url, token="", timeout=0):
        return "   "

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(response=_response("- 摘要"))
    plugin = _FakePlugin(context)

    assert await service.summarize_changes(plugin, "push", _push_payload()) is None
    assert context.llm_calls == []


@pytest.mark.asyncio
async def test_summarize_changes_skips_without_provider(monkeypatch):
    """无法解析 Provider 时不拉 diff、不调用 LLM，避免白耗 GitHub 限额."""
    fetch_calls = []

    async def fake_fetch(url, token="", timeout=0):
        fetch_calls.append(url)
        return "diff"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(provider_error=RuntimeError("no provider"))
    plugin = _FakePlugin(context, llm_provider_id="")

    assert await service.summarize_changes(plugin, "push", _push_payload()) is None
    assert fetch_calls == []
    assert context.llm_calls == []


@pytest.mark.asyncio
async def test_summarize_changes_timeout_returns_none(monkeypatch):
    """LLM 超时应返回 None，而不是阻塞后台任务."""

    async def fake_fetch(url, token="", timeout=0):
        return "diff"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(response=_response("late"), delay=1)
    plugin = _FakePlugin(context, agent_timeout=0.01)

    assert await service.summarize_changes(plugin, "push", _push_payload()) is None


@pytest.mark.asyncio
async def test_summarize_changes_llm_error_returns_none(monkeypatch):
    """LLM 抛异常时应返回 None，不影响模板消息."""

    async def fake_fetch(url, token="", timeout=0):
        return "diff"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(generate_error=RuntimeError("llm down"))
    plugin = _FakePlugin(context)

    assert await service.summarize_changes(plugin, "push", _push_payload()) is None


@pytest.mark.asyncio
async def test_summarize_changes_empty_completion_returns_none(monkeypatch):
    """LLM 返回空内容时不发送空摘要."""

    async def fake_fetch(url, token="", timeout=0):
        return "diff"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(response=_response(" \n\t "))
    plugin = _FakePlugin(context)

    assert await service.summarize_changes(plugin, "push", _push_payload()) is None


@pytest.mark.asyncio
async def test_summarize_changes_truncates_long_diff(monkeypatch):
    """超出字符上限的 diff 应被截断并标注，提示词仍可正常生成."""

    async def fake_fetch(url, token="", timeout=0):
        return "A" * 100 + "TAIL"

    monkeypatch.setattr(service, "fetch_diff", fake_fetch)
    context = _FakeContext(response=_response("- 摘要"))
    plugin = _FakePlugin(context, code_review_max_diff_chars=10)

    await service.summarize_changes(plugin, "push", _push_payload())

    prompt = context.llm_calls[0]["prompt"]
    assert "A" * 10 in prompt
    assert "TAIL" not in prompt
    assert "diff 已截断" in prompt


def test_build_summary_prompt_falls_back_for_non_positive_limit():
    """上限为非正数（宿主缺键兜底为 0）时使用默认值，避免空 diff."""
    prompt = service.build_summary_prompt("ctx", "diff-content", 0)

    assert "diff-content" in prompt


def test_build_summary_prompt_includes_pr_context():
    """PR 场景的提示词应带上仓库、编号、标题与分支信息."""
    prompt = service.build_summary_prompt(
        service._build_event_context("pull_request", _pr_payload()), "diff", 100
    )

    assert "owner/repo" in prompt
    assert "PR #12" in prompt
    assert "Add feature" in prompt
    assert "feature → main" in prompt


def test_clean_summary_limits_output_length():
    """摘要总长度超过上限时截断，避免刷屏."""
    summary = service._clean_summary("x" * (service.MAX_SUMMARY_CHARS + 10))

    assert len(summary) == service.MAX_SUMMARY_CHARS

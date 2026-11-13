"""Tests for the core webhook plugin."""

import asyncio
import errno
import hashlib
import hmac
import json
import socket

import aiohttp
import pytest

import src.core.plugin as plugin_module
import src.services.diff_summary_service as diff_summary_service
import src.services.llm_service as llm_service
from src.core.config import normalize_webhook_path
from src.core.constants import DEFAULT_WEBHOOK_PATH
from src.core.plugin import GitHubWebhookPlugin

# 测试只监听回环地址，避免在 CI 环境中暴露端口；DevSkim 把该地址字面量
# 视为调试代码，这里是有意为之，故就地抑制 DS162092。
_LOOPBACK_HOST = "127.0.0.1"  # DevSkim: ignore DS162092


class _FakeContext:
    """记录 send_message 调用的最小 Context 桩。"""

    def __init__(self, result=True, error=None):
        self.sent = []
        self.result = result
        self.error = error

    async def send_message(self, umo, chain):
        """记录发送目标与消息链；可按需模拟失败。"""
        if self.error:
            raise self.error
        self.sent.append((umo, chain))
        return self.result


class _FakeMessageChain:
    """记录消息组件的最小 MessageChain 桩。"""

    def __init__(self, components):
        self.components = components


class _FakePlain:
    """记录文本内容的最小 Plain 组件桩。"""

    def __init__(self, text):
        self.text = text


class _FakeRequest:
    """最小 aiohttp 请求桩：只需要 headers 与 read()."""

    def __init__(self, payload=b"", headers=None, read_error=None):
        self._payload = payload
        self.headers = headers or {}
        self._read_error = read_error

    async def read(self):
        """返回请求体；read_error 非空时模拟读取失败。"""
        if self._read_error:
            raise self._read_error
        return self._payload


@pytest.fixture(autouse=True)
def _patch_message_components(monkeypatch):
    """用可断言的桩替换宿主消息组件，保证测试与宿主实现解耦."""
    monkeypatch.setattr(
        plugin_module.api, "MessageChain", _FakeMessageChain, raising=False
    )
    monkeypatch.setattr(plugin_module, "Plain", _FakePlain)


def _make_plugin(context=None, **overrides):
    """按默认配置构造插件实例，可用关键字覆盖单个配置项."""
    config = {
        "port": 0,
        "bind_host": _LOOPBACK_HOST,
        "webhook_path": DEFAULT_WEBHOOK_PATH,
        "target_umo": "test:GroupMessage:1",
        "repo_routes": "",
        "enabled_events": "",
        "webhook_secret": "",
        "rate_limit": 0,
        "enable_agent": False,
        "llm_provider_id": "",
        "agent_timeout": 60,
        "agent_system_prompt": "",
        "enable_code_review": False,
        "github_token": "",
        "code_review_max_diff_chars": 12000,
    }
    config.update(overrides)
    return GitHubWebhookPlugin(context or _FakeContext(), config)


def _sign(payload: bytes, secret: str) -> str:
    """按 GitHub 的 sha256= 格式生成签名头."""
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _request(event_type="ping", payload=b"{}", **kwargs):
    """构造带 X-GitHub-Event 头的请求桩."""
    headers = {"X-GitHub-Event": event_type}
    return _FakeRequest(payload, headers=headers, **kwargs)


def test_rate_limiter_follows_config():
    """rate_limit <= 0 时不装配限流器，正数时装配."""
    assert _make_plugin(rate_limit=0).rate_limiter is None
    assert _make_plugin(rate_limit=5).rate_limiter is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_WEBHOOK_PATH),
        ("", DEFAULT_WEBHOOK_PATH),
        ("   ", DEFAULT_WEBHOOK_PATH),
        (123, DEFAULT_WEBHOOK_PATH),
        ("webhook", "/webhook"),
        ("/github/webhook", "/github/webhook"),
        ("/github/webhook/", "/github/webhook"),
        ("  /custom  ", "/custom"),
        ("/", "/"),
        ("/bad path", DEFAULT_WEBHOOK_PATH),
        ("/hook?x=1", DEFAULT_WEBHOOK_PATH),
        ("/hook/{id}", DEFAULT_WEBHOOK_PATH),
    ],
)
def test_normalize_webhook_path(raw, expected):
    """路径配置应补前导斜杠、去尾斜杠，非法值回落到默认路径."""
    assert normalize_webhook_path(raw) == expected


def test_custom_webhook_path_is_registered():
    """自定义 webhook_path 应成为唯一注册的 POST 路由，旧路径不再注册."""
    plugin = _make_plugin(webhook_path="/github/webhook")

    paths = {
        route.resource.canonical
        for route in plugin.app.router.routes()
        if route.resource is not None
    }

    assert paths == {"/github/webhook"}


@pytest.mark.asyncio
async def test_ping_returns_pong():
    """ping 事件直接回 Pong，不发送消息."""
    context = _FakeContext()
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("ping"))

    assert response.status == 200
    assert response.text == "Pong"
    assert context.sent == []


@pytest.mark.asyncio
async def test_invalid_json_returns_400():
    """请求体不是合法 JSON 时返回 400."""
    plugin = _make_plugin()

    response = await plugin.handle_webhook(_request("push", b"{not json"))

    assert response.status == 400
    assert response.text == "Invalid JSON"


@pytest.mark.asyncio
async def test_read_failure_returns_400():
    """读取请求体抛异常时返回 400，而不是 500."""
    plugin = _make_plugin()
    request = _request("push", read_error=RuntimeError("connection reset"))

    response = await plugin.handle_webhook(request)

    assert response.status == 400
    assert response.text == "Failed to read request"


@pytest.mark.asyncio
async def test_rate_limit_exceeded_returns_429():
    """超过限流阈值时返回 429，并带上重试相关响应头."""
    plugin = _make_plugin(rate_limit=1)

    first = await plugin.handle_webhook(_request("ping"))
    second = await plugin.handle_webhook(_request("ping"))

    assert first.status == 200
    assert second.status == 429
    assert second.headers["X-RateLimit-Limit"] == "1"
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert int(second.headers["Retry-After"]) >= 1
    assert "Retry after" in second.text


@pytest.mark.asyncio
async def test_valid_signature_is_accepted():
    """配置了 secret 且签名正确时放行请求."""
    payload = b'{"action": "opened"}'
    secret = "top-secret"
    plugin = _make_plugin(webhook_secret=secret)
    request = _request("ping", payload)
    request.headers["X-Hub-Signature-256"] = _sign(payload, secret)

    response = await plugin.handle_webhook(request)

    assert response.status == 200
    assert response.text == "Pong"


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected():
    """配置了 secret 但签名错误时返回 401，且不处理事件."""
    plugin = _make_plugin(webhook_secret="top-secret")
    request = _request("ping", b"{}")
    request.headers["X-Hub-Signature-256"] = "sha256=deadbeef"

    response = await plugin.handle_webhook(request)

    assert response.status == 401
    assert response.text == "Invalid signature"


@pytest.mark.asyncio
@pytest.mark.parametrize("header_mode", ["absent", "empty"])
async def test_missing_signature_header_is_rejected(header_mode):
    """配置了 secret 但没有签名头（缺失或空值）时返回 401，不再静默放行."""
    context = _FakeContext()
    plugin = _make_plugin(context, webhook_secret="top-secret")
    request = _request("push", b"{}")
    if header_mode == "empty":
        request.headers["X-Hub-Signature-256"] = ""

    response = await plugin.handle_webhook(request)

    assert response.status == 401
    assert response.text == "Missing signature"
    # 未通过验签的请求不得进入事件处理与消息发送
    assert context.sent == []


@pytest.mark.asyncio
async def test_signature_ignored_without_secret():
    """未配置 secret 时不校验签名，错误的签名头也不会阻断请求."""
    plugin = _make_plugin(webhook_secret="")
    request = _request("ping")
    request.headers["X-Hub-Signature-256"] = "sha256=deadbeef"

    response = await plugin.handle_webhook(request)

    assert response.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "handler_name"),
    [
        ("push", "handle_push_event"),
        ("issues", "handle_issues_event"),
        ("pull_request", "handle_pull_request_event"),
    ],
)
async def test_known_events_are_routed(monkeypatch, event_type, handler_name):
    """push / issues / pull_request 应交给对应 handler 并发送其消息."""
    context = _FakeContext()
    plugin = _make_plugin(context)
    calls = []

    async def fake_handler(data, ctx):
        calls.append((data, ctx))
        return "formatted-message"

    monkeypatch.setattr(plugin_module, handler_name, fake_handler)
    payload = {"action": "opened", "event_type": event_type}
    request = _request(event_type, json.dumps(payload).encode("utf-8"))

    response = await plugin.handle_webhook(request)
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert response.text == "OK"
    assert calls == [(payload, context)]
    assert len(context.sent) == 1
    umo, chain = context.sent[0]
    assert umo == "test:GroupMessage:1"
    assert [component.text for component in chain.components] == ["formatted-message"]


@pytest.mark.asyncio
async def test_webhook_returns_before_background_processing_finishes(monkeypatch):
    """handler 尚未完成时 webhook 已返回 200，证明处理与响应已解耦."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(data, ctx):
        started.set()
        await release.wait()
        return "slow-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", slow_handler)
    context = _FakeContext()
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("push"))
    await started.wait()

    # 后台任务已启动但被阻塞，响应此时已经返回且尚未发送消息
    assert response.status == 200
    assert context.sent == []

    release.set()
    await plugin.wait_for_background_tasks()

    assert len(context.sent) == 1


@pytest.mark.asyncio
async def test_terminate_cancels_background_tasks(monkeypatch):
    """插件卸载时取消仍在执行的后台事件处理任务."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hung_handler(data, ctx):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(plugin_module, "handle_push_event", hung_handler)
    plugin = _make_plugin()

    await plugin.handle_webhook(_request("push"))
    await started.wait()
    await plugin.terminate()

    assert cancelled.is_set()
    assert plugin._background_tasks == set()


@pytest.mark.asyncio
async def test_webhook_routes_message_by_repository(monkeypatch):
    """repo_routes 命中时按仓库把消息分发到对应 UMO 列表."""

    async def fake_handler(data, ctx):
        return "formatted-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    context = _FakeContext()
    plugin = _make_plugin(
        context,
        repo_routes='{"owner/repo": ["umo:1", "umo:2"]}',
        target_umo="fallback",
    )
    payload = {"repository": {"full_name": "owner/repo"}}

    response = await plugin.handle_webhook(
        _request("push", json.dumps(payload).encode("utf-8"))
    )
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert [umo for umo, _ in context.sent] == ["umo:1", "umo:2"]


@pytest.mark.asyncio
async def test_webhook_falls_back_for_unknown_repository(monkeypatch):
    """仓库未命中任何路由时使用 target_umo 兜底."""

    async def fake_handler(data, ctx):
        return "formatted-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    context = _FakeContext()
    plugin = _make_plugin(
        context,
        repo_routes='{"owner/repo": ["umo:1"]}',
        target_umo="fallback",
    )
    payload = {"repository": {"full_name": "other/repo"}}

    response = await plugin.handle_webhook(
        _request("push", json.dumps(payload).encode("utf-8"))
    )
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert [umo for umo, _ in context.sent] == ["fallback"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event_type", "handler_name"),
    [
        ("release", "handle_release_event"),
        ("workflow_run", "handle_workflow_run_event"),
        ("star", "handle_star_event"),
        ("watch", "handle_star_event"),
        ("issue_comment", "handle_issue_comment_event"),
    ],
)
async def test_new_events_are_routed(monkeypatch, event_type, handler_name):
    """release / workflow_run / star / watch / issue_comment 应交给对应 handler."""
    context = _FakeContext()
    plugin = _make_plugin(context)
    calls = []

    async def fake_handler(data, ctx):
        calls.append((data, ctx))
        return "formatted-message"

    monkeypatch.setattr(plugin_module, handler_name, fake_handler)
    payload = {"action": "created"}
    request = _request(event_type, json.dumps(payload).encode("utf-8"))

    response = await plugin.handle_webhook(request)
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert calls == [(payload, context)]
    assert len(context.sent) == 1


@pytest.mark.asyncio
async def test_disabled_event_is_ignored(monkeypatch):
    """enabled_events 未包含的事件不进入 handler，也不发送消息."""
    calls = []

    async def fake_handler(data, ctx):
        calls.append(data)
        return "should-not-send"

    monkeypatch.setattr(plugin_module, "handle_issues_event", fake_handler)
    context = _FakeContext()
    plugin = _make_plugin(context, enabled_events="push,release")

    response = await plugin.handle_webhook(_request("issues", b"{}"))

    assert response.status == 200
    assert response.text == "OK"
    assert calls == []
    assert context.sent == []


@pytest.mark.asyncio
async def test_ping_is_not_affected_by_event_whitelist():
    """ping 只做健康检查，不受 enabled_events 白名单影响."""
    plugin = _make_plugin(enabled_events="push")

    response = await plugin.handle_webhook(_request("ping"))

    assert response.status == 200
    assert response.text == "Pong"


@pytest.mark.asyncio
async def test_unknown_event_is_ignored():
    """未知事件只记日志，返回 200 且不发送消息."""
    context = _FakeContext()
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("deployment", b"{}"))

    assert response.status == 200
    assert response.text == "OK"
    assert context.sent == []


@pytest.mark.asyncio
async def test_handler_returning_none_sends_nothing(monkeypatch):
    """handler 返回 None（例如空推送）时不发送消息."""

    async def fake_handler(data, ctx):
        return None

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    context = _FakeContext()
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert context.sent == []


@pytest.mark.asyncio
async def test_handler_exception_does_not_fail_webhook(monkeypatch):
    """后台 handler 抛异常时只记日志，webhook 已先行返回 200 且不发送消息."""

    async def fake_handler(data, ctx):
        raise RuntimeError("handler boom")

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    context = _FakeContext()
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert response.text == "OK"
    assert context.sent == []


@pytest.mark.asyncio
async def test_agent_mode_delegates_to_llm_service(monkeypatch):
    """enable_agent 为真时交由 llm_service 改写并发送."""
    calls = []

    async def fake_send_with_agent(
        plugin_instance, message, data, event_type, target_umos=None
    ):
        calls.append((plugin_instance, message, data, event_type, target_umos))

    async def fake_handler(data, ctx):
        return "template-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    monkeypatch.setattr(llm_service, "send_with_agent", fake_send_with_agent)
    context = _FakeContext()
    plugin = _make_plugin(context, enable_agent=True)
    payload = {"ref": "refs/heads/main"}

    response = await plugin.handle_webhook(
        _request("push", json.dumps(payload).encode("utf-8"))
    )
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert calls == [
        (plugin, "template-message", payload, "push", ["test:GroupMessage:1"])
    ]
    # 直接发送由 llm_service 负责，plugin 这里不应再发一次
    assert context.sent == []


@pytest.mark.asyncio
async def test_code_review_summary_sent_after_template(monkeypatch):
    """启用代码摘要时，模板消息之后追加一条独立的 AI 摘要消息."""
    summary_calls = []

    async def fake_summary(plugin_instance, event_type, data):
        summary_calls.append((plugin_instance, event_type, data))
        return "- 新增登录接口\n- 修复空指针"

    async def fake_handler(data, ctx):
        return "template-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    monkeypatch.setattr(diff_summary_service, "summarize_changes", fake_summary)
    context = _FakeContext()
    plugin = _make_plugin(context, enable_code_review=True)
    payload = {"ref": "refs/heads/main"}

    response = await plugin.handle_webhook(
        _request("push", json.dumps(payload).encode("utf-8"))
    )
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert summary_calls == [(plugin, "push", payload)]
    assert [
        (umo, [component.text for component in chain.components])
        for umo, chain in context.sent
    ] == [
        ("test:GroupMessage:1", ["template-message"]),
        ("test:GroupMessage:1", ["🤖 AI 变更摘要\n- 新增登录接口\n- 修复空指针"]),
    ]


@pytest.mark.asyncio
async def test_code_review_disabled_skips_summary(monkeypatch):
    """未启用代码摘要时不应调用 diff 摘要服务."""
    calls = []

    async def fake_summary(plugin_instance, event_type, data):
        calls.append(event_type)
        return "- 摘要"

    async def fake_handler(data, ctx):
        return "template-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    monkeypatch.setattr(diff_summary_service, "summarize_changes", fake_summary)
    context = _FakeContext()
    plugin = _make_plugin(context, enable_code_review=False)

    await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert calls == []
    assert len(context.sent) == 1


@pytest.mark.asyncio
async def test_code_review_failure_keeps_template_message(monkeypatch):
    """摘要返回 None 时只发送模板消息，不发送空摘要."""

    async def fake_summary(plugin_instance, event_type, data):
        return None

    async def fake_handler(data, ctx):
        return "template-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    monkeypatch.setattr(diff_summary_service, "summarize_changes", fake_summary)
    context = _FakeContext()
    plugin = _make_plugin(context, enable_code_review=True)

    await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert [[c.text for c in chain.components] for _, chain in context.sent] == [
        ["template-message"]
    ]


@pytest.mark.asyncio
async def test_code_review_exception_keeps_template_message(monkeypatch):
    """摘要服务意外抛异常时模板消息已发出，且不影响后台任务收敛."""

    async def fake_summary(plugin_instance, event_type, data):
        raise RuntimeError("summary boom")

    async def fake_handler(data, ctx):
        return "template-message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    monkeypatch.setattr(diff_summary_service, "summarize_changes", fake_summary)
    context = _FakeContext()
    plugin = _make_plugin(context, enable_code_review=True)

    await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert [[c.text for c in chain.components] for _, chain in context.sent] == [
        ["template-message"]
    ]
    assert plugin._background_tasks == set()


@pytest.mark.asyncio
async def test_send_failure_does_not_break_webhook(monkeypatch):
    """发送消息失败时应吞掉异常，webhook 仍返回 200."""

    async def fake_handler(data, ctx):
        return "message"

    monkeypatch.setattr(plugin_module, "handle_push_event", fake_handler)
    context = _FakeContext(error=RuntimeError("platform down"))
    plugin = _make_plugin(context)

    response = await plugin.handle_webhook(_request("push"))
    await plugin.wait_for_background_tasks()

    assert response.status == 200
    assert response.text == "OK"


@pytest.mark.asyncio
async def test_send_message_skips_without_target_umo():
    """未配置 target_umo 时不调用平台发送."""
    context = _FakeContext()
    plugin = _make_plugin(context, target_umo="")

    await plugin.send_message("hello")

    assert context.sent == []


@pytest.mark.asyncio
async def test_send_message_fans_out_to_route_targets():
    """路由解析出多个 UMO 时逐一发送同一消息."""
    context = _FakeContext()
    plugin = _make_plugin(context)

    await plugin.send_message("hello", ["umo:1", "umo:2"])

    assert [umo for umo, _ in context.sent] == ["umo:1", "umo:2"]
    assert [
        [component.text for component in chain.components] for _, chain in context.sent
    ] == [["hello"], ["hello"]]


@pytest.mark.asyncio
async def test_send_message_skips_explicitly_muted_repo():
    """repo_routes 命中空数组时不发送，也不回落到 target_umo."""
    context = _FakeContext()
    plugin = _make_plugin(context)

    await plugin.send_message("hello", [])

    assert context.sent == []


@pytest.mark.asyncio
async def test_send_to_one_target_failure_does_not_block_others(monkeypatch):
    """单个目标发送失败时仍继续发送其它目标."""
    context = _FakeContext()
    plugin = _make_plugin(context)
    original_send = context.send_message

    async def flaky_send(umo, chain):
        if umo == "umo:bad":
            raise RuntimeError("platform down")
        return await original_send(umo, chain)

    monkeypatch.setattr(context, "send_message", flaky_send)

    await plugin.send_message("hello", ["umo:bad", "umo:good"])

    assert [umo for umo, _ in context.sent] == ["umo:good"]


@pytest.mark.asyncio
async def test_send_message_builds_plain_message_chain():
    """发送时应把文本包成 Plain 组件并交给目标会话."""
    context = _FakeContext()
    plugin = _make_plugin(context)

    await plugin.send_message("hello")

    assert len(context.sent) == 1
    umo, chain = context.sent[0]
    assert umo == "test:GroupMessage:1"
    assert [component.text for component in chain.components] == ["hello"]


@pytest.mark.asyncio
async def test_send_message_tolerates_false_result():
    """平台返回 False（未找到适配器）时只告警，不抛异常."""
    context = _FakeContext(result=False)
    plugin = _make_plugin(context)

    await plugin.send_message("hello")

    assert len(context.sent) == 1


@pytest.mark.asyncio
async def test_start_server_and_terminate():
    """插件应能在配置地址上启动、重入重启并正常释放资源."""
    plugin = _make_plugin(port=0, bind_host=_LOOPBACK_HOST)
    try:
        await plugin.start_server()
        first_site = plugin.site
        assert first_site is not None
        assert plugin.runner is not None

        # 重复启动会先清理旧实例，再创建新的 site
        await plugin.start_server()
        assert plugin.site is not None
        assert plugin.site is not first_site
    finally:
        await plugin.terminate()


def _occupy_port():
    """占用回环地址上的随机端口，返回 (socket, port)."""
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupied.bind((_LOOPBACK_HOST, 0))
    occupied.listen(1)
    return occupied, occupied.getsockname()[1]


def test_is_address_in_use_matches_errno():
    """只有 EADDRINUSE（含 Windows 的 WSAEADDRINUSE）才判定为端口占用."""
    assert plugin_module._is_address_in_use(OSError(errno.EADDRINUSE, "in use"))
    assert not plugin_module._is_address_in_use(OSError(errno.EACCES, "denied"))
    assert not plugin_module._is_address_in_use(OSError(errno.EADDRNOTAVAIL, "bad"))


def test_is_address_in_use_ignores_oserror_without_errno():
    """errno 为 None 的 OSError 不得被误判为端口占用（POSIX 无 WSAEADDRINUSE）."""
    assert not plugin_module._is_address_in_use(OSError("bind failed"))


def test_is_address_in_use_matches_windows_winerror():
    """Windows 上仅带 winerror=10048 的 OSError 也应判定为端口占用."""
    error = OSError("bind failed")
    error.winerror = 10048  # type: ignore[attr-defined]
    assert plugin_module._is_address_in_use(error)


@pytest.mark.asyncio
async def test_start_server_retries_after_port_released(monkeypatch):
    """端口短暂被占用时按退避重试，释放后应能成功启动."""
    monkeypatch.setattr(plugin_module, "SERVER_START_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(plugin_module, "SERVER_START_RETRY_BASE_DELAY", 0.05)
    occupied, port = _occupy_port()

    async def release_port():
        await asyncio.sleep(0.01)
        occupied.close()

    release_task = asyncio.create_task(release_port())
    plugin = _make_plugin(port=port, bind_host=_LOOPBACK_HOST)
    try:
        await plugin.start_server()
        assert plugin.site is not None
    finally:
        await plugin.terminate()
        occupied.close()
        await release_task


@pytest.mark.asyncio
async def test_start_server_fails_after_retries_when_port_stays_busy(monkeypatch):
    """端口持续被占用时，重试耗尽后抛出 OSError 并清理 runner/site."""
    monkeypatch.setattr(plugin_module, "SERVER_START_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(plugin_module, "SERVER_START_RETRY_BASE_DELAY", 0)
    occupied, port = _occupy_port()
    plugin = _make_plugin(port=port, bind_host=_LOOPBACK_HOST)
    try:
        with pytest.raises(OSError):
            await plugin.start_server()
        assert plugin.site is None
        assert plugin.runner is None
    finally:
        occupied.close()


@pytest.mark.asyncio
async def test_start_server_does_not_retry_on_other_bind_errors(monkeypatch):
    """非端口占用的绑定错误（如权限不足）应立即失败，不做重试."""
    attempts = []

    class _FailingSite:
        """任意绑定都抛 EACCES 的 TCPSite 桩，用于断言不做重试."""

        def __init__(self, runner, host, port):
            """记录每次尝试绑定的地址."""
            attempts.append((host, port))

        async def start(self):
            """模拟权限不足的绑定失败."""
            raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(plugin_module.web, "TCPSite", _FailingSite)
    plugin = _make_plugin(port=8080, bind_host=_LOOPBACK_HOST)

    with pytest.raises(OSError):
        await plugin.start_server()

    assert len(attempts) == 1
    assert plugin.runner is None
    assert plugin.site is None


@pytest.mark.asyncio
async def test_custom_webhook_path_serves_requests():
    """自定义路径实际生效：新路径返回 Pong，默认 /webhook 返回 404."""
    plugin = _make_plugin(
        port=0,
        bind_host=_LOOPBACK_HOST,
        webhook_path="/github/webhook",
    )
    try:
        await plugin.start_server()
        assert plugin.runner is not None
        port = plugin.runner.addresses[0][1]
        base_url = f"http://{_LOOPBACK_HOST}:{port}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/github/webhook",
                headers={"X-GitHub-Event": "ping"},
                data=b"{}",
            ) as response:
                assert response.status == 200
                assert await response.text() == "Pong"

            async with session.post(
                f"{base_url}/webhook",
                headers={"X-GitHub-Event": "ping"},
                data=b"{}",
            ) as response:
                assert response.status == 404
    finally:
        await plugin.terminate()

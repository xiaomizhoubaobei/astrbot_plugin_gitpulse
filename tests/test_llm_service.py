"""Tests for the optional LLM message enhancement service."""

import asyncio
from types import SimpleNamespace

import pytest

from src.services.llm_service import send_with_agent


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
        """返回当前会话的默认 provider，或模拟获取失败."""
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
    """记录 send_message 调用的最小插件桩."""

    def __init__(self, context, **cfg_overrides):
        self.context = context
        cfg = {
            "llm_provider_id": "provider-1",
            "agent_system_prompt": "",
            "agent_timeout": 60,
            "target_umo": "test:GroupMessage:1",
        }
        cfg.update(cfg_overrides)
        self.cfg = SimpleNamespace(**cfg)
        self.sent = []

    async def send_message(self, message):
        """记录最终发送的文本."""
        self.sent.append(message)


def _response(text):
    """构造带 completion_text 的 LLM 响应桩."""
    return SimpleNamespace(completion_text=text)


@pytest.mark.asyncio
async def test_generated_message_is_used():
    """LLM 正常返回时应发送清理后的生成文本，而不是模板."""
    context = _FakeContext(response=_response("  Hello\n\n  World  \n"))
    plugin = _FakePlugin(context, agent_system_prompt="be nice")

    await send_with_agent(plugin, "template", {"ref": "refs/heads/main"}, "push")

    assert plugin.sent == ["Hello\nWorld"]
    assert context.llm_calls[0]["chat_provider_id"] == "provider-1"
    assert context.llm_calls[0]["system_prompt"] == "be nice"
    assert "template" in context.llm_calls[0]["prompt"]


@pytest.mark.asyncio
async def test_empty_provider_id_falls_back_to_current_provider():
    """未配置 provider 时应查询当前会话的默认 provider."""
    context = _FakeContext(provider_id="default-provider", response=_response("hi"))
    plugin = _FakePlugin(context, llm_provider_id="")

    await send_with_agent(plugin, "template", {}, "push")

    assert plugin.sent == ["hi"]
    assert context.llm_calls[0]["chat_provider_id"] == "default-provider"
    # 未配置 system prompt 时传 None，避免把空串交给 LLM
    assert context.llm_calls[0]["system_prompt"] is None


@pytest.mark.asyncio
async def test_provider_lookup_failure_falls_back_to_template():
    """获取默认 provider 失败时应回落到模板消息."""
    context = _FakeContext(provider_error=RuntimeError("no provider"))
    plugin = _FakePlugin(context, llm_provider_id="")

    await send_with_agent(plugin, "template", {}, "push")

    assert plugin.sent == ["template"]
    assert context.llm_calls == []


@pytest.mark.asyncio
async def test_llm_timeout_falls_back_to_template():
    """LLM 超时应回落到模板消息，不能阻塞 webhook."""
    context = _FakeContext(response=_response("late"), delay=1)
    plugin = _FakePlugin(context, agent_timeout=0.01)

    await send_with_agent(plugin, "template", {}, "push")

    assert plugin.sent == ["template"]


@pytest.mark.asyncio
async def test_empty_completion_falls_back_to_template():
    """LLM 返回空文本（含仅空白）时应回落到模板消息."""
    for text in (None, "", " \n\t "):
        context = _FakeContext(response=_response(text) if text is not None else None)
        plugin = _FakePlugin(context)

        await send_with_agent(plugin, "template", {}, "push")

        assert plugin.sent == ["template"]


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_template():
    """LLM 调用抛异常时应回落到模板消息."""
    context = _FakeContext(generate_error=RuntimeError("llm down"))
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, "template", {}, "issues")

    assert plugin.sent == ["template"]


_TEMPLATE_WITH_URL = "✅ PR 已合并\n📎 https://github.com/owner/repo/pull/7"


@pytest.mark.asyncio
async def test_all_urls_preserved_uses_generated_message():
    """LLM 输出保留全部核心链接时，使用润色后的消息."""
    context = _FakeContext(
        response=_response("🎉 PR 已合并 https://github.com/owner/repo/pull/7")
    )
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, _TEMPLATE_WITH_URL, {}, "pull_request")

    assert plugin.sent == ["🎉 PR 已合并 https://github.com/owner/repo/pull/7"]


@pytest.mark.asyncio
async def test_missing_url_falls_back_to_template():
    """LLM 输出丢失核心链接时，回退到模板消息，保证信息不失真."""
    context = _FakeContext(response=_response("🎉 PR 已合并，快去看看"))
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, _TEMPLATE_WITH_URL, {}, "pull_request")

    assert plugin.sent == [_TEMPLATE_WITH_URL]


@pytest.mark.asyncio
async def test_partial_url_loss_falls_back_to_template():
    """模板含多个链接时，只要丢失任意一个就回退."""
    template = (
        "🔀 2 commits\n"
        "📎 https://github.com/owner/repo/commit/abc\n"
        "🌿 https://github.com/owner/repo/tree/main"
    )
    context = _FakeContext(
        response=_response("推送了 2 个提交 https://github.com/owner/repo/commit/abc")
    )
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, template, {}, "push")

    assert plugin.sent == [template]


@pytest.mark.asyncio
async def test_markdown_wrapped_url_counts_as_preserved():
    """LLM 用 Markdown 包裹链接时，提取结果应忽略尾随括号与标点."""
    context = _FakeContext(
        response=_response("🎉 [PR](https://github.com/owner/repo/pull/7) 已合并")
    )
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, _TEMPLATE_WITH_URL, {}, "pull_request")

    assert plugin.sent == ["🎉 [PR](https://github.com/owner/repo/pull/7) 已合并"]


@pytest.mark.asyncio
async def test_short_truncated_output_falls_back_to_template():
    """极短截断输出通常会丢失链接，应触发模板兜底."""
    context = _FakeContext(response=_response("完成"))
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, _TEMPLATE_WITH_URL, {}, "pull_request")

    assert plugin.sent == [_TEMPLATE_WITH_URL]


@pytest.mark.asyncio
async def test_template_without_url_accepts_generated_message():
    """模板本身无链接时不做 URL 比对，正常使用生成结果."""
    context = _FakeContext(response=_response("简短回复"))
    plugin = _FakePlugin(context)

    await send_with_agent(plugin, "plain template", {}, "ping")

    assert plugin.sent == ["简短回复"]

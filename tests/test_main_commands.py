"""Tests for the in-chat GitHub command handler registered in ``main.py``."""

import pytest

import main as entry
import src.commands.github_command as command_module


class _FakeEvent:
    """最小消息事件桩：只提供 handler 用到的三个方法."""

    def __init__(self, message_str="", admin=True):
        self._message_str = message_str
        self._admin = admin

    def get_message_str(self):
        """返回消息纯文本（唤醒前缀已被宿主剥掉）."""
        return self._message_str

    def is_admin(self):
        """是否管理员."""
        return self._admin

    def plain_result(self, text):
        """把回显文本包成可断言的对象."""
        return {"text": text}


def _make_entry(**overrides):
    """构造插件入口，配置项可用关键字覆盖."""
    config = {
        "port": 8080,
        "target_umo": "test:GroupMessage:1",
        "enable_github_commands": True,
        "github_token": "t0ken",
        "github_command_repos": "owner/repo",
        "github_command_default_repo": "owner/repo",
    }
    config.update(overrides)
    return entry.PluginEntry(context=object(), config=config)


async def _collect(gen):
    """收集 async generator 产出的所有结果."""
    return [item async for item in gen]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("github merge 12 squash", ("merge", "12", "squash")),
        ("github close owner/repo#5", ("close", "owner/repo#5", "")),
        ("github comment 12 你好 世界", ("comment", "12", "你好 世界")),
        ("github", ("", "", "")),
        ("close 12", ("close", "12", "")),
    ],
)
def test_parse_command_line(text, expected):
    """指令行解析：动作、目标与保留空格的附加正文."""
    assert command_module.parse_command_line(text) == expected


@pytest.mark.asyncio
async def test_command_replies_help_when_disabled():
    """未启用时不执行任何写操作，只回提示."""
    plugin = _make_entry(enable_github_commands=False)

    results = await _collect(plugin.github_command(_FakeEvent("github close 12")))

    assert "未启用" in results[0]["text"]


@pytest.mark.asyncio
async def test_command_replies_help_without_action():
    """缺少动作时回帮助信息."""
    plugin = _make_entry()

    results = await _collect(plugin.github_command(_FakeEvent("github")))

    assert "/github merge" in results[0]["text"]


@pytest.mark.asyncio
async def test_command_blocks_non_admin_by_default():
    """默认仅管理员可执行写操作，普通成员收到提示."""
    plugin = _make_entry()

    results = await _collect(
        plugin.github_command(_FakeEvent("github close 12", admin=False))
    )

    assert "管理员" in results[0]["text"]


@pytest.mark.asyncio
async def test_command_refuses_repo_outside_whitelist():
    """不在授权列表的仓库被拒绝，且不会发起任何 HTTP 请求."""
    plugin = _make_entry(github_command_default_repo="other/repo")

    results = await _collect(plugin.github_command(_FakeEvent("github close 12")))

    assert "不在授权列表" in results[0]["text"]


@pytest.mark.asyncio
async def test_command_refuses_without_token():
    """未配置 Token 时拒绝执行并提示维护者."""
    plugin = _make_entry(github_token="")

    results = await _collect(plugin.github_command(_FakeEvent("github close 12")))

    assert "GitHub Token" in results[0]["text"]


@pytest.mark.asyncio
async def test_command_reports_success(monkeypatch):
    """管理员执行成功时回显动作、仓库#编号与链接."""
    calls = {}

    async def _fake_merge(repo, number, token, method):
        calls.update(repo=repo, number=number, token=token, method=method)
        return command_module.CommandOutcome(
            ok=True,
            detail="合并方式：squash",
            url="https://github.com/owner/repo/pull/12",
        )

    monkeypatch.setattr(command_module, "merge_pull_request", _fake_merge)
    plugin = _make_entry()

    results = await _collect(plugin.github_command(_FakeEvent("github merge 12")))

    assert "owner/repo#12" in results[0]["text"]
    assert calls["method"] == "squash"
    assert calls["token"] == "t0ken"


@pytest.mark.asyncio
async def test_command_surfaces_unexpected_error_as_text(monkeypatch):
    """服务层抛出非预期异常时也只回显人话，不外泄堆栈."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(command_module, "set_issue_state", _boom)
    plugin = _make_entry()

    results = await _collect(plugin.github_command(_FakeEvent("github close 12")))

    assert "执行失败" in results[0]["text"]
    assert "boom" not in results[0]["text"]

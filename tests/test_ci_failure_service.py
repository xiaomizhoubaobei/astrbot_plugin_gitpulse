"""Tests for the optional CI failure analysis service."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.services.ci_failure_service as service
from src.core.constants import MAX_CI_LOG_TAIL_BYTES


class _FakeContent:
    """最小 aiohttp 响应内容桩，支持分块读取."""

    def __init__(self, payload: bytes):
        self.payload = payload

    async def iter_chunked(self, n: int):
        """按块返回预设内容."""
        for start in range(0, len(self.payload), n):
            yield self.payload[start : start + n]


class _FakeResponse:
    """最小 aiohttp 响应桩，可配置状态码、响应体与响应头."""

    def __init__(
        self,
        status: int = 200,
        text: str = "",
        payload: bytes = b"",
        headers: dict | None = None,
    ):
        self.status = status
        self._text = text
        self.content = _FakeContent(payload)
        self.headers = headers or {}

    async def __aenter__(self):
        """支持 `async with session.get(...) as response`."""
        return self

    async def __aexit__(self, *exc_info):
        """退出上下文时不吞异常."""
        return False

    async def text(self):
        """返回预设文本响应体."""
        return self._text


def _raise_response(exc):
    """构造一个在调用时抛异常的响应工厂."""

    def _factory():
        raise exc

    return _factory


def _patch_session(monkeypatch, routes):
    """替换 aiohttp.ClientSession，按 URL 路由响应并记录请求."""
    calls = []

    class _FakeSession:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def get(self, url, headers=None, allow_redirects=None):
            calls.append(
                {
                    "url": url,
                    "headers": headers or {},
                    "allow_redirects": allow_redirects,
                }
            )
            target = routes.get(url)
            if target is None:
                return _FakeResponse(status=404)
            if callable(target):
                return target()
            return target

    monkeypatch.setattr(service.aiohttp, "ClientSession", _FakeSession)
    return calls


class _Cfg:
    """最小 PluginConfig 桩：只暴露归因链路用到的属性."""

    def __init__(self, **overrides):
        self.github_token = "t0ken"
        self.llm_provider_id = "provider-1"
        self.agent_timeout = 60
        self.ci_failure_max_log_chars = 6000
        self.ci_failure_system_prompt = ""
        for key, value in overrides.items():
            setattr(self, key, value)


class _Context:
    """最小 LLM Context 桩，按需返回 provider 或生成结果."""

    def __init__(
        self,
        response=None,
        error=None,
        delay=0,
        default_provider="provider-1",
    ):
        self.response = response
        self.error = error
        self.delay = delay
        self.default_provider = default_provider
        self.llm_calls = []

    async def get_current_chat_provider_id(self, umo):
        """返回预设的默认 Provider."""
        return self.default_provider

    async def llm_generate(self, chat_provider_id, prompt, system_prompt=None):
        """记录调用并按配置返回结果或抛异常."""
        self.llm_calls.append(
            {
                "provider": chat_provider_id,
                "prompt": prompt,
                "system_prompt": system_prompt,
            }
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response


class _Plugin:
    """最小插件实例桩."""

    def __init__(self, context=None, **overrides):
        self.cfg = _Cfg(**overrides)
        self.context = context or _Context()


def _payload(conclusion="failure", repo="owner/repo", run_id=123):
    """构造 workflow_run 失败 payload."""
    return {
        "action": "completed",
        "repository": {"full_name": repo},
        "workflow_run": {
            "id": run_id,
            "name": "CI",
            "conclusion": conclusion,
            "head_branch": "main",
            "event": "push",
            "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        },
    }


def _jobs_url(repo="owner/repo", run_id=123):
    """构造 jobs 接口地址."""
    return (
        f"{service.GITHUB_API_BASE}/repos/{repo}/actions/runs/"
        f"{run_id}/jobs?filter=latest&per_page=100"
    )


def _log_url(repo="owner/repo", job_id=456):
    """构造 job 日志接口地址."""
    return f"{service.GITHUB_API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs"


JOBS_JSON = """{
  "jobs": [
    {
      "id": 456,
      "name": "build",
      "conclusion": "failure",
      "steps": [
        {"name": "Checkout", "conclusion": "success"},
        {"name": "Run tests", "conclusion": "failure"}
      ]
    },
    {"id": 789, "name": "lint", "conclusion": "success", "steps": []}
  ]
}"""


# --------------------------------------------------------------------------
# 事件筛选与上下文
# --------------------------------------------------------------------------


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_select_failed_run_accepts_failure_conclusions(conclusion):
    """failure / timed_out / startup_failure 都应进入归因."""
    assert service.select_failed_run(_payload(conclusion)) == ("owner/repo", 123)


@pytest.mark.parametrize(
    "conclusion",
    ["success", "cancelled", "skipped", "neutral", "action_required", None],
)
def test_select_failed_run_ignores_non_failure_conclusions(conclusion):
    """非失败结论没有可分析的失败日志，应直接跳过."""
    assert service.select_failed_run(_payload(conclusion)) is None


def test_select_failed_run_validates_payload():
    """动作、仓库名与 run id 任一非法时都返回 None."""
    assert service.select_failed_run({**_payload(), "action": "requested"}) is None
    assert service.select_failed_run(_payload(repo="../evil")) is None
    assert service.select_failed_run(_payload(repo="noslash")) is None
    assert service.select_failed_run(_payload(run_id=0)) is None
    assert service.select_failed_run(_payload(run_id="123")) is None
    assert service.select_failed_run({"action": "completed"}) is None


def test_select_failed_run_rejects_malformed_repository():
    """repository 不是映射、或 full_name 不是字符串时返回 None."""
    payload = _payload()
    payload["repository"] = "owner/repo"
    assert service.select_failed_run(payload) is None

    payload = _payload()
    payload["repository"] = {"full_name": 123}
    assert service.select_failed_run(payload) is None


def test_build_failure_context_tolerates_missing_workflow_run():
    """workflow_run 缺失或不合法时仍能生成上下文."""
    context = service.build_failure_context({"repository": {"full_name": "owner/repo"}})

    assert "owner/repo" in context
    assert "Unknown" in context


def test_clean_analysis_truncates_long_output():
    """归因结论超过上限时截断并加省略号."""
    from src.core.constants import MAX_CI_ANALYSIS_CHARS

    analysis = service._clean_analysis("x" * (MAX_CI_ANALYSIS_CHARS + 10))

    assert len(analysis) == MAX_CI_ANALYSIS_CHARS
    assert analysis.endswith("…")


def test_build_failure_context_includes_metadata():
    """上下文应包含仓库、工作流、分支、触发事件、结论与链接."""
    context = service.build_failure_context(_payload())

    assert "owner/repo" in context
    assert "CI" in context
    assert "main" in context
    assert "push" in context
    assert "failure" in context
    assert "actions/runs/123" in context


# --------------------------------------------------------------------------
# 脱敏与提示词
# --------------------------------------------------------------------------


def test_redact_secrets_covers_common_credentials():
    """常见 Token、密码与私钥格式应被替换为 ***."""
    # 拆开拼接 PEM 头尾，避免测试夹具被 detect-private-key 误判为真实私钥。
    private_key = (
        "-----BEGIN " + "RSA PRIVATE KEY-----\nsecret\n"
        "-----END " + "RSA PRIVATE KEY-----"
    )
    text = (
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
        "github_pat_11ABCDEFG0123456789_abcdefghij\n"
        "AKIAABCDEFGHIJKLMNOP\n"
        "password: hunter2\n"
        "Authorization: Bearer abc.def.ghi\n"
        f"{private_key}\n"
        "normal log line"
    )

    redacted = service.redact_secrets(text)

    assert "ghp_" not in redacted
    assert "github_pat_" not in redacted
    assert "AKIA" not in redacted
    assert "hunter2" not in redacted
    assert "Bearer abc.def.ghi" not in redacted
    assert "PRIVATE KEY" not in redacted
    assert "normal log line" in redacted


def test_build_analysis_prompt_keeps_tail_and_marks_truncation():
    """日志超长时只保留尾部，并显式标注截断."""
    logs = "HEAD-" + "x" * 100 + "-TAIL"
    prompt = service.build_analysis_prompt("CONTEXT", logs, max_log_chars=20)

    assert "CONTEXT" in prompt
    assert "-TAIL" in prompt
    assert "HEAD-" not in prompt
    assert "日志已截断" in prompt


def test_build_analysis_prompt_keeps_short_logs_intact():
    """日志未超长时不加截断标记."""
    prompt = service.build_analysis_prompt("CONTEXT", "short log", max_log_chars=100)

    assert "short log" in prompt
    assert "日志已截断" not in prompt


def test_build_analysis_prompt_falls_back_for_invalid_limit():
    """非正数上限回落到默认值，不产生空日志."""
    logs = "x" * 10
    prompt = service.build_analysis_prompt("CONTEXT", logs, max_log_chars=0)

    assert logs in prompt


# --------------------------------------------------------------------------
# jobs / 日志抓取
# --------------------------------------------------------------------------


def test_parse_failed_jobs_filters_and_reports_failed_steps():
    """只保留失败 job，并带上失败 step 名称."""
    jobs = service._parse_failed_jobs(json.loads(JOBS_JSON))

    assert jobs == [
        {"id": 456, "name": "build", "steps": ["Run tests"]},
    ]


def test_parse_failed_jobs_skips_invalid_entries():
    """非映射、非法 id、非列表都会被安全跳过."""
    assert service._parse_failed_jobs(None) == []
    assert service._parse_failed_jobs({"jobs": "nope"}) == []
    assert (
        service._parse_failed_jobs(
            {
                "jobs": [
                    "not-a-job",
                    {"id": "456", "conclusion": "failure"},
                    {"id": -1, "conclusion": "failure"},
                    {"id": 1, "conclusion": "success"},
                ]
            }
        )
        == []
    )


def test_parse_failed_jobs_skips_non_mapping_steps():
    """失败 job 的 steps 里混入非法条目时只跳过该条."""
    jobs = service._parse_failed_jobs(
        {
            "jobs": [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "failure",
                    "steps": [123, {"name": "Run tests", "conclusion": "failure"}],
                }
            ]
        }
    )

    assert jobs == [{"id": 1, "name": "build", "steps": ["Run tests"]}]


def test_parse_failed_jobs_limits_to_three():
    """失败 job 数量有上限，避免一次归因调用过多 API."""
    payload = {
        "jobs": [
            {"id": index, "name": f"job-{index}", "conclusion": "failure"}
            for index in range(1, 6)
        ]
    }

    assert len(service._parse_failed_jobs(payload)) == 3


@pytest.mark.asyncio
async def test_fetch_failed_jobs_returns_empty_on_http_error(monkeypatch):
    """jobs 接口非 200 时返回空列表."""
    _patch_session(monkeypatch, {_jobs_url(): _FakeResponse(status=500)})

    assert await service.fetch_failed_jobs("owner/repo", 123, "t0ken") == []


@pytest.mark.asyncio
async def test_fetch_failed_jobs_handles_network_error(monkeypatch):
    """jobs 接口网络异常时返回空列表."""
    _patch_session(
        monkeypatch,
        {_jobs_url(): _raise_response(RuntimeError("connection reset"))},
    )

    assert await service.fetch_failed_jobs("owner/repo", 123, "t0ken") == []


@pytest.mark.asyncio
async def test_fetch_failed_jobs_survives_non_json_body(monkeypatch):
    """非 JSON 响应只告警，不抛异常."""
    _patch_session(monkeypatch, {_jobs_url(): _FakeResponse(status=200, text="<html>")})

    assert await service.fetch_failed_jobs("owner/repo", 123, "t0ken") == []


@pytest.mark.asyncio
async def test_fetch_failed_jobs_parses_response(monkeypatch):
    """成功响应解析出失败 job 与失败 step."""
    _patch_session(
        monkeypatch, {_jobs_url(): _FakeResponse(status=200, text=JOBS_JSON)}
    )

    jobs = await service.fetch_failed_jobs("owner/repo", 123, "t0ken")

    assert jobs == [{"id": 456, "name": "build", "steps": ["Run tests"]}]


@pytest.mark.asyncio
async def test_fetch_job_log_follows_https_redirect_without_token(monkeypatch):
    """跟随 302 到签名地址时不携带 Authorization，只带 Range 取尾部."""
    signed_url = "https://signed.example/log"
    calls = _patch_session(
        monkeypatch,
        {
            _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
            signed_url: _FakeResponse(status=206, payload=b"tail-of-log"),
        },
    )

    log = await service.fetch_job_log("owner/repo", 456, "t0ken")

    assert log == "tail-of-log"
    assert calls[0]["headers"]["Authorization"] == "Bearer t0ken"
    assert calls[0]["allow_redirects"] is False
    assert "Authorization" not in calls[1]["headers"]
    assert calls[1]["headers"]["Range"] == f"bytes=-{MAX_CI_LOG_TAIL_BYTES}"


@pytest.mark.asyncio
async def test_fetch_job_log_rejects_non_https_redirect(monkeypatch):
    """拒绝跟随非 HTTPS 的重定向地址."""
    calls = _patch_session(
        monkeypatch,
        {
            _log_url(): _FakeResponse(
                status=302, headers={"Location": "http://evil.example/log"}
            )
        },
    )

    assert await service.fetch_job_log("owner/repo", 456, "t0ken") == ""
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fetch_job_log_handles_redirect_target_error(monkeypatch):
    """签名地址返回非 200/206 时返回空串."""
    signed_url = "https://signed.example/log"
    _patch_session(
        monkeypatch,
        {
            _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
            signed_url: _FakeResponse(status=404),
        },
    )

    assert await service.fetch_job_log("owner/repo", 456, "t0ken") == ""


@pytest.mark.asyncio
async def test_fetch_job_log_accepts_inline_body(monkeypatch):
    """日志接口直接返回 200 时同样读取尾部."""
    _patch_session(
        monkeypatch,
        {_log_url(): _FakeResponse(status=200, payload=b"inline-log")},
    )

    assert await service.fetch_job_log("owner/repo", 456, "t0ken") == "inline-log"


@pytest.mark.asyncio
async def test_fetch_job_log_handles_http_and_network_errors(monkeypatch):
    """HTTP 错误与网络异常都返回空串."""
    _patch_session(monkeypatch, {_log_url(): _FakeResponse(status=404)})
    assert await service.fetch_job_log("owner/repo", 456, "t0ken") == ""

    _patch_session(
        monkeypatch,
        {_log_url(): _raise_response(RuntimeError("connection reset"))},
    )
    assert await service.fetch_job_log("owner/repo", 456, "t0ken") == ""


@pytest.mark.asyncio
async def test_fetch_job_log_keeps_tail_when_oversized(monkeypatch):
    """超大日志只保留最后一段，并按下载上限提前停止."""
    monkeypatch.setattr(service, "MAX_CI_LOG_TAIL_BYTES", 4)
    monkeypatch.setattr(service, "MAX_CI_LOG_DOWNLOAD_BYTES", 8)
    _patch_session(
        monkeypatch,
        {_log_url(): _FakeResponse(status=200, payload=b"0123456789ABCDEF")},
    )

    log = await service.fetch_job_log("owner/repo", 456, "t0ken")

    assert log == "CDEF"


# --------------------------------------------------------------------------
# 归因编排
# --------------------------------------------------------------------------


def _analysis_context(text="根因：缺少依赖\n- 安装 requirements.txt"):
    """构造返回固定归因结论的 LLM Context."""
    return _Context(response=SimpleNamespace(completion_text=text))


@pytest.mark.asyncio
async def test_analyze_workflow_failure_generates_analysis(monkeypatch):
    """成功链路：拉 jobs → 拉日志 → 脱敏 → 调 LLM，返回清理后的结论."""
    signed_url = "https://signed.example/log"
    _patch_session(
        monkeypatch,
        {
            _jobs_url(): _FakeResponse(status=200, text=JOBS_JSON),
            _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
            signed_url: _FakeResponse(
                status=206,
                payload=b"error: ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            ),
        },
    )
    context = _analysis_context()
    plugin = _Plugin(context=context, ci_failure_max_log_chars=6000)

    analysis = await service.analyze_workflow_failure(plugin, _payload())

    assert analysis == "根因：缺少依赖\n- 安装 requirements.txt"
    assert len(context.llm_calls) == 1
    prompt = context.llm_calls[0]["prompt"]
    assert "owner/repo" in prompt
    assert "Job: build" in prompt
    assert "Run tests" in prompt
    assert "ghp_" not in prompt
    assert "***" in prompt


@pytest.mark.asyncio
async def test_analyze_workflow_failure_respects_custom_prompt_and_limit(monkeypatch):
    """自定义系统提示词与日志字符上限都应生效."""
    signed_url = "https://signed.example/log"
    _patch_session(
        monkeypatch,
        {
            _jobs_url(): _FakeResponse(status=200, text=JOBS_JSON),
            _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
            signed_url: _FakeResponse(status=206, payload=b"A" * 100 + b"TAIL"),
        },
    )
    context = _analysis_context()
    plugin = _Plugin(
        context=context,
        ci_failure_max_log_chars=10,
        ci_failure_system_prompt="CUSTOM PROMPT",
    )

    await service.analyze_workflow_failure(plugin, _payload())

    call = context.llm_calls[0]
    assert call["system_prompt"] == "CUSTOM PROMPT"
    assert "TAIL" in call["prompt"]
    assert "日志已截断" in call["prompt"]


@pytest.mark.asyncio
async def test_analyze_workflow_failure_uses_default_prompt(monkeypatch):
    """未配置系统提示词时使用内置默认值."""
    signed_url = "https://signed.example/log"
    _patch_session(
        monkeypatch,
        {
            _jobs_url(): _FakeResponse(status=200, text=JOBS_JSON),
            _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
            signed_url: _FakeResponse(status=206, payload=b"log"),
        },
    )
    context = _analysis_context()
    plugin = _Plugin(context=context)

    await service.analyze_workflow_failure(plugin, _payload())

    assert context.llm_calls[0]["system_prompt"] == service.CI_FAILURE_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_analyze_workflow_failure_requires_token_and_provider(monkeypatch):
    """缺 Token 或缺 LLM Provider 时不发任何请求."""
    calls = _patch_session(monkeypatch, {})

    assert (
        await service.analyze_workflow_failure(_Plugin(github_token=""), _payload())
        is None
    )

    context = _Context(default_provider="")
    plugin = _Plugin(context=context, llm_provider_id="")
    assert await service.analyze_workflow_failure(plugin, _payload()) is None
    assert calls == []


@pytest.mark.asyncio
async def test_analyze_workflow_failure_skips_when_no_jobs_or_logs(monkeypatch):
    """没有失败 job 或读不到日志时返回 None."""
    _patch_session(
        monkeypatch,
        {_jobs_url(): _FakeResponse(status=200, text='{"jobs": []}')},
    )
    assert await service.analyze_workflow_failure(_Plugin(), _payload()) is None

    _patch_session(
        monkeypatch,
        {
            _jobs_url(): _FakeResponse(status=200, text=JOBS_JSON),
            _log_url(): _FakeResponse(status=404),
        },
    )
    assert await service.analyze_workflow_failure(_Plugin(), _payload()) is None


@pytest.mark.asyncio
async def test_analyze_workflow_failure_handles_llm_errors(monkeypatch):
    """LLM 异常 / 超时 / 空输出都返回 None."""
    signed_url = "https://signed.example/log"
    routes = {
        _jobs_url(): _FakeResponse(status=200, text=JOBS_JSON),
        _log_url(): _FakeResponse(status=302, headers={"Location": signed_url}),
        signed_url: _FakeResponse(status=206, payload=b"log"),
    }

    _patch_session(monkeypatch, routes)
    assert (
        await service.analyze_workflow_failure(
            _Plugin(context=_Context(error=RuntimeError("boom"))), _payload()
        )
        is None
    )

    _patch_session(monkeypatch, routes)
    assert (
        await service.analyze_workflow_failure(
            _Plugin(context=_Context(delay=0.2), agent_timeout=0.01), _payload()
        )
        is None
    )

    _patch_session(monkeypatch, routes)
    assert (
        await service.analyze_workflow_failure(
            _Plugin(context=_analysis_context("   ")), _payload()
        )
        is None
    )


@pytest.mark.asyncio
async def test_analyze_workflow_failure_ignores_non_failure_event(monkeypatch):
    """非失败事件不进入归因链路."""
    calls = _patch_session(monkeypatch, {})

    assert (
        await service.analyze_workflow_failure(
            _Plugin(), _payload(conclusion="success")
        )
        is None
    )
    assert calls == []

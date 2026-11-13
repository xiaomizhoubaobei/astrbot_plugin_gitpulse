"""Tests for scripts/vector_ingest.py（OSS 向量导入脚本）."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "vector_ingest.py"
_SPEC = importlib.util.spec_from_file_location("vector_ingest", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
vector_ingest = importlib.util.module_from_spec(_SPEC)
# dataclass 在 exec 时需要能从 sys.modules 找到所属模块
sys.modules["vector_ingest"] = vector_ingest
_SPEC.loader.exec_module(vector_ingest)


def _options(**overrides):
    """构造切片函数需要的最小参数集."""
    values = {
        "chunk_size": 500,
        "chunk_overlap": 50,
        "key_prefix": "cnb",
        "kind": "knowledge",
        "tags": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_uri_encode_encodes_arn_but_keeps_slashes():
    """ARN 中的冒号必须编码，路径分隔符保留."""
    encoded = vector_ingest._uri_encode(
        "acs:ossvector:cn-shanghai:123:demo", keep_slash=True
    )

    assert encoded == "acs%3Aossvector%3Acn-shanghai%3A123%3Ademo"


def test_build_canonical_request_matches_oss_v4_layout():
    """规范请求必须包含 ARN 路径、裸查询串与空的附加头行."""
    headers = {
        "content-md5": "abc==",  # DevSkim: ignore DS126858
        "content-type": "application/json",
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": "20260101T000000Z",
    }

    canonical = vector_ingest.build_canonical_request(
        "POST",
        "/acs%3Aossvector%3Acn-shanghai%3A123%3Ademo/",
        "putVectors",
        headers,
    )

    assert canonical == (
        "POST\n"
        "/acs%3Aossvector%3Acn-shanghai%3A123%3Ademo/\n"
        "putVectors\n"
        "content-md5:abc==\n"  # DevSkim: ignore DS126858
        "content-type:application/json\n"
        "x-oss-content-sha256:UNSIGNED-PAYLOAD\n"
        "x-oss-date:20260101T000000Z\n"
        "\n"
        "\n"
        "UNSIGNED-PAYLOAD"
    )


def test_sign_v4_returns_expected_authorization():
    """固定输入下的签名应保持稳定（防止重构破坏 V4 算法）."""
    headers = {
        "content-md5": "abc==",  # DevSkim: ignore DS126858
        "content-type": "application/json",
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": "20260101T000000Z",
    }

    authorization = vector_ingest.sign_v4(
        "POST",
        "/acs%3Aossvector%3Acn-shanghai%3A123%3Ademo/",
        "putVectors",
        headers,
        "AKID",
        "SECRET",
        "cn-shanghai",
        "20260101T000000Z",
    )

    assert authorization == (
        "OSS4-HMAC-SHA256 Credential=AKID/20260101/cn-shanghai/oss/"
        "aliyun_v4_request,Signature="
        "7fcd2c6bcabc00d6a1888f9b48920aff"  # DevSkim: ignore DS173237
        "76481a8b89f2701f6ffb8eba1bfa1ece"  # DevSkim: ignore DS173237
    )


def test_chunk_text_respects_size_limit():
    """每个切片都不得超过 chunk_size."""
    text = ("第一段内容。" * 40 + "\n\n") * 3

    chunks = vector_ingest.chunk_text(text, chunk_size=100, overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_chunk_text_prefers_sentence_boundary():
    """窗口内存在句号时应在句号后断开."""
    text = "A" * 60 + "。" + "B" * 60

    chunks = vector_ingest.chunk_text(text, chunk_size=70, overlap=10)

    assert chunks[0] == "A" * 60 + "。"
    assert chunks[0].endswith("。")


def test_chunk_text_short_input_is_single_chunk():
    """短文本不切片."""
    assert vector_ingest.chunk_text("hello", 100, 10) == ["hello"]


def test_chunk_text_rejects_non_positive_size():
    """chunk_size 非正数属于配置错误."""
    with pytest.raises(vector_ingest.IngestError):
        vector_ingest.chunk_text("hello", 0, 0)


def test_chunks_from_jsonl_supports_text_content_and_custom_key(tmp_path):
    """JSONL 同时支持 text/content 字段、自定义 key 与 metadata."""
    path = tmp_path / "notes.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {"content": "内容一", "metadata": {"repo": "owner/repo"}},
                    ensure_ascii=False,
                ),
                json.dumps({"text": "内容二", "key": "custom-key"}, ensure_ascii=False),
            ]
        ),
        encoding="utf-8",
    )

    chunks = vector_ingest._chunks_from_jsonl(path, _options())

    assert [chunk.text for chunk in chunks] == ["内容一", "内容二"]
    assert chunks[0].metadata["repo"] == "owner/repo"
    assert chunks[0].metadata["chunk"] == 0
    assert chunks[0].metadata["total"] == 2
    assert chunks[0].key.startswith("cnb:")
    assert chunks[1].key == "custom-key"


def test_chunks_from_text_sets_title_and_tags(tmp_path):
    """Markdown 文件应提取标题并带上公共元数据."""
    path = tmp_path / "doc.md"
    path.write_text("# 标题\n\n正文内容。", encoding="utf-8")

    chunks = vector_ingest._chunks_from_text(path, _options(tags=["cnb", "gpg"]))

    assert len(chunks) == 1
    assert chunks[0].metadata["title"] == "标题"
    assert chunks[0].metadata["tags"] == ["cnb", "gpg"]
    assert chunks[0].metadata["kind"] == "knowledge"
    assert chunks[0].metadata["source"].endswith("doc.md")


def test_default_key_is_stable_and_index_sensitive():
    """同一来源同一序号的 key 必须稳定，不同序号不冲突."""
    first = vector_ingest._default_key("cnb", "docs/a.md", 0)
    second = vector_ingest._default_key("cnb", "docs/a.md", 0)
    other = vector_ingest._default_key("cnb", "docs/a.md", 1)

    assert first == second
    assert first != other


def test_dry_run_does_not_require_credentials(monkeypatch, tmp_path, capsys):
    """dry-run 只切片，不读取凭证、不发网络请求."""
    for name in (
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
        "DASHSCOPE_API_KEY",
        "OSS_VECTOR_ACCOUNT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "note.md"
    path.write_text("# 标题\n\n正文内容。", encoding="utf-8")

    exit_code = vector_ingest.main(["--dry-run", str(path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "dry-run" in captured.out + captured.err


def test_real_run_reports_missing_credentials(monkeypatch, tmp_path, capsys):
    """非 dry-run 缺少凭证时应给出明确错误而不是抛出堆栈."""
    for name in (
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
        "DASHSCOPE_API_KEY",
        "OSS_VECTOR_ACCOUNT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "note.md"
    path.write_text("正文内容。", encoding="utf-8")

    exit_code = vector_ingest.main([str(path)])

    assert exit_code == 2
    assert "缺少必要配置" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--embed-batch-size", "0"], "embed_batch_size"),
        (["--embed-batch-size", "11"], "embed_batch_size"),
        (["--vector-batch-size", "0"], "vector_batch_size"),
        (["--vector-batch-size", "501"], "vector_batch_size"),
        (["--dimension", "0"], "dimension"),
    ],
)
def test_invalid_batch_or_dimension_returns_clean_error(
    tmp_path, capsys, flags, message
):
    """批次/维度非法时应返回 IngestError 而不是 ValueError 或死循环."""
    path = tmp_path / "note.md"
    path.write_text("正文内容。", encoding="utf-8")

    exit_code = vector_ingest.main(["--dry-run", *flags, str(path)])

    assert exit_code == 2
    assert message in capsys.readouterr().err

"""Tests for the webhook HMAC-SHA256 signature verifier."""

import hashlib
import hmac

import pytest

from src.utils.verify_signature import verify_signature

_SECRET = "top-secret"
_PAYLOAD = b'{"action": "opened"}'


def _sign(payload: bytes, secret: str) -> str:
    """按 GitHub 的 sha256= 格式生成签名头."""
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_returns_true():
    """正确签名应通过校验."""
    assert verify_signature(_PAYLOAD, _sign(_PAYLOAD, _SECRET), _SECRET) is True


def test_tampered_payload_returns_false():
    """签名与请求体不匹配时应拒绝（payload 被篡改）."""
    signature = _sign(_PAYLOAD, _SECRET)

    assert verify_signature(b'{"action": "closed"}', signature, _SECRET) is False


def test_wrong_secret_returns_false():
    """使用不同 secret 计算出的签名应被拒绝."""
    signature = _sign(_PAYLOAD, "another-secret")

    assert verify_signature(_PAYLOAD, signature, _SECRET) is False


@pytest.mark.parametrize(
    "signature_header",
    [
        "",
        "sha256=",
        "sha256=deadbeef",
        "deadbeef",
        "sha1=deadbeef",
        "sha256=DEADBEEF",
    ],
)
def test_missing_or_malformed_header_returns_false(signature_header):
    """缺失、空值或格式错误的签名头一律拒绝."""
    assert verify_signature(_PAYLOAD, signature_header, _SECRET) is False


def test_missing_secret_returns_false():
    """未配置 secret 时不应判定为验签通过."""
    assert verify_signature(_PAYLOAD, _sign(_PAYLOAD, _SECRET), "") is False


def test_uses_constant_time_comparison(monkeypatch):
    """验签必须走 hmac.compare_digest，避免退化为 == 引入时序侧信道."""
    real_compare = hmac.compare_digest
    calls = []

    def spy(expected: str, actual: str) -> bool:
        calls.append((expected, actual))
        return real_compare(expected, actual)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    assert verify_signature(_PAYLOAD, _sign(_PAYLOAD, _SECRET), _SECRET) is True
    assert len(calls) == 1
    assert calls[0][0] == calls[0][1]

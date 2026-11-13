#!/usr/bin/env python3
"""把知识文档写入阿里云 OSS 向量 Bucket（纯标准库实现，无需 SDK/ossutil）.

流程：
1. 将 Markdown / 文本 / JSONL 文档切片；
2. 调用百炼 ``text-embedding-v4`` HTTP API 生成向量；
3. 按 OSS V4（``OSS4-HMAC-SHA256``）自行签名，调用 ``PutVectors`` HTTP API 写入。

之所以手写签名：运行镜像未安装 OSS/百炼 SDK，且不希望为一次性导入引入额外依赖。
凭证只从环境变量读取，不写入命令行参数，避免出现在进程列表中。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DASHSCOPE_EMBED_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
    "text-embedding/text-embedding"
)

DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSION = 1024
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_EMBED_BATCH_SIZE = 10
DEFAULT_VECTOR_BATCH_SIZE = 100
MAX_EMBED_BATCH_SIZE = 10
MAX_VECTOR_BATCH_SIZE = 500
MAX_TOP_K = 500
MAX_LIST_PAGE_SIZE = 1000
MAX_DELETE_BATCH = 500
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".jsonl"})
# 切片时优先在这些边界处断开，避免把句子/段落从中间截断。
_SENTENCE_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ")
# 仓库标识：至少两段（owner/repo 或 org/group/repo），只允许安全字符。
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")


class IngestError(RuntimeError):
    """脚本可预期的失败（配置、网络、API 返回错误等）."""


@dataclass(frozen=True)
class OssConfig:
    """OSS 向量 Bucket 的连接与签名配置."""

    bucket: str
    index: str
    region: str
    account_id: str
    endpoint: str
    access_key_id: str
    access_key_secret: str
    timeout: float = 60.0
    retries: int = 3

    @property
    def host(self) -> str:
        """返回向量 API 的虚拟主机域名（Bucket 名需带账号 ID 后缀）."""
        return f"{self.bucket}-{self.account_id}.{self.endpoint}"

    @property
    def bucket_arn(self) -> str:
        """返回签名规范 URI 中使用的向量 Bucket ARN."""
        return f"acs:ossvector:{self.region}:{self.account_id}:{self.bucket}"


@dataclass
class IngestOptions:
    """一次导入任务的参数集合."""

    oss: OssConfig
    dashscope_api_key: str
    repo: str = ""
    group: str = ""
    model: str = DEFAULT_MODEL
    dimension: int = DEFAULT_DIMENSION
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE
    vector_batch_size: int = DEFAULT_VECTOR_BATCH_SIZE
    key_prefix: str = "cnb"
    kind: str = "knowledge"
    tags: list[str] = field(default_factory=list)
    dry_run: bool = False
    quiet: bool = False


@dataclass(frozen=True)
class Chunk:
    """一条待写入向量的文本及其元数据."""

    key: str
    text: str
    metadata: dict[str, Any]


def _uri_encode(value: str, keep_slash: bool = False) -> str:
    """按 OSS V4 规则做 URI 编码（保留 ``/`` 时用于路径）."""
    safe = "/-_.~" if keep_slash else "-_.~"
    return urllib.parse.quote(value, safe=safe)


def normalize_repo(repo: str) -> str | None:
    """校验并归一化仓库标识；非法时返回 None.

    支持 ``owner/repo`` 与 CNB 的 ``org/group/repo`` 形式，拒绝空段与
    ``.`` / ``..`` 路径段，避免污染签名与 metadata。
    """
    value = repo.strip().strip("/")
    if not _REPO_PATTERN.fullmatch(value):
        return None
    if any(segment in {".", ".."} for segment in value.split("/")):
        return None
    return value


def repo_group(repo: str) -> str:
    """从仓库标识推导分组：CNB 取第二段，GitHub 风格取 owner."""
    parts = repo.split("/")
    return parts[1] if len(parts) >= 3 else parts[0]


def repo_hash(repo: str) -> str:
    """返回仓库标识的稳定短哈希，用于主键命名空间隔离."""
    return hashlib.sha256(repo.lower().encode("utf-8")).hexdigest()[:16]


def parse_repo_from_remote(url: str) -> str | None:
    """从 git remote URL 解析仓库标识，支持 https / ssh / scp 形式."""
    value = url.strip()
    if not value:
        return None
    if "://" not in value and ":" in value:
        # scp 形式：git@host:owner/repo.git
        _, _, path = value.partition(":")
    else:
        path = urllib.parse.urlparse(value).path
    path = path.strip().strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return normalize_repo(path)


def detect_repo() -> str | None:
    """从当前仓库的 origin remote 自动识别仓库标识."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_repo_from_remote(result.stdout)


def build_canonical_request(
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: dict[str, str],
    payload_marker: str = "UNSIGNED-PAYLOAD",
) -> str:
    """构造 OSS V4 规范请求.

    ``canonical_query`` 直接传入服务端期望的原始查询串（如 ``putVectors``），
    不在这里做 ``name=value`` 归一化：向量 API 的裸查询串没有等号。
    """
    canonical_headers = "".join(
        f"{name.lower()}:{value.strip()}\n"
        for name, value in sorted(
            (str(name).lower(), str(value)) for name, value in headers.items()
        )
    )
    return "\n".join(
        [
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            "",  # 未使用 AdditionalHeaders，保留空行
            payload_marker,
        ]
    )


def sign_v4(
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: dict[str, str],
    access_key_id: str,
    access_key_secret: str,
    region: str,
    timestamp: str,
) -> str:
    """计算 OSS V4 签名并返回 ``Authorization`` 头.

    ``timestamp`` 必须是 UTC 的 ``YYYYMMDDTHHMMSSZ`` 格式。
    """
    sign_date = timestamp[:8]
    canonical_request = build_canonical_request(
        method, canonical_uri, canonical_query, headers
    )
    scope = f"{sign_date}/{region}/oss/aliyun_v4_request"
    string_to_sign = "\n".join(
        [
            "OSS4-HMAC-SHA256",
            timestamp,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    date_key = hmac.new(
        ("aliyun_v4" + access_key_secret).encode("utf-8"),
        sign_date.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"oss", hashlib.sha256).digest()
    signing_key = hmac.new(service_key, b"aliyun_v4_request", hashlib.sha256).digest()
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"OSS4-HMAC-SHA256 Credential={access_key_id}/{scope}," f"Signature={signature}"
    )


def _now_timestamp() -> str:
    """返回 OSS V4 所需的 UTC 时间戳."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _http_request(
    request: urllib.request.Request, timeout: float, retries: int
) -> bytes:
    """发送 HTTPS 请求；对限流/5xx 做指数退避重试，其余错误直接抛出."""
    scheme = urllib.parse.urlparse(request.full_url).scheme.lower()
    if scheme != "https":
        raise IngestError(f"仅允许 https 请求，拒绝 {scheme or '未知'} 协议")

    last_error = ""
    for attempt in range(retries + 1):
        try:
            # 入口已强制 https，故屏蔽 B310 对 file:/自定义协议的静态告警。
            with urllib.request.urlopen(  # nosec B310
                request, timeout=timeout
            ) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {error.code}: {body[:1200]}"
            if error.code in RETRYABLE_STATUS and attempt < retries:
                time.sleep(2**attempt)
                continue
            raise IngestError(last_error) from error
        except urllib.error.URLError as error:
            last_error = f"网络错误: {error.reason}"
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise IngestError(last_error) from error
    raise IngestError(last_error or "请求失败")


def _oss_vector_request(
    config: OssConfig, operation: str, body: dict[str, Any]
) -> dict[str, Any]:
    """调用一个 OSS 向量 API，返回解析后的 JSON 响应.

    ``operation`` 同时作为裸查询串（如 ``putVectors`` / ``queryVectors``）
    与签名参数；向量 API 不使用 ``name=value`` 形式。
    """
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    # OSS 协议要求 Content-MD5，不能替换为更安全的算法；仅用于传输校验。
    content_md5 = base64.b64encode(  # DevSkim: ignore DS126858
        hashlib.md5(  # DevSkim: ignore DS126858
            raw_body, usedforsecurity=False
        ).digest()
    ).decode("ascii")
    timestamp = _now_timestamp()
    canonical_uri = f"/{_uri_encode(config.bucket_arn, keep_slash=True)}/"
    headers = {
        "content-md5": content_md5,  # DevSkim: ignore DS126858
        "content-type": "application/json",
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": timestamp,
    }
    authorization = sign_v4(
        method="POST",
        canonical_uri=canonical_uri,
        canonical_query=operation,
        headers=headers,
        access_key_id=config.access_key_id,
        access_key_secret=config.access_key_secret,
        region=config.region,
        timestamp=timestamp,
    )
    request = urllib.request.Request(
        f"https://{config.host}/?{operation}",
        data=raw_body,
        method="POST",
        headers={
            "Content-MD5": content_md5,  # DevSkim: ignore DS126858
            "Content-Type": "application/json",
            "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
            "x-oss-date": timestamp,
            "Authorization": authorization,
            "User-Agent": "gitpulse-vector-ingest/1.0",
        },
    )
    raw = _http_request(request, config.timeout, config.retries)
    if not raw.strip():
        return {}
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        preview = raw.decode("utf-8", errors="replace")[:500]
        raise IngestError(f"OSS 响应解析失败: {error}; 响应: {preview}") from error
    if not isinstance(response, dict):
        raise IngestError("OSS 响应不是 JSON 对象")
    return response


def put_vectors(
    config: OssConfig, index: str, vectors: Sequence[dict[str, Any]]
) -> None:
    """调用 ``PutVectors`` 写入一批向量（单批最多 500 条）."""
    if not vectors:
        return
    if len(vectors) > MAX_VECTOR_BATCH_SIZE:
        raise IngestError(f"单批向量数 {len(vectors)} 超过上限 {MAX_VECTOR_BATCH_SIZE}")

    _oss_vector_request(
        config, "putVectors", {"indexName": index, "vectors": list(vectors)}
    )


def query_vectors(
    config: OssConfig,
    index: str,
    vector: Sequence[float],
    top_k: int,
    repo: str | None = None,
) -> list[dict[str, Any]]:
    """调用 ``QueryVectors`` 做近似最近邻检索，返回匹配向量列表.

    传入 ``repo`` 时使用服务端 metadata filter 只召回该仓库的向量，
    实现仓库级检索隔离；``repo=None`` 表示跨仓库检索。
    """
    if not 0 < top_k <= MAX_TOP_K:
        raise IngestError(f"top_k 必须在 1~{MAX_TOP_K} 之间")

    body: dict[str, Any] = {
        "indexName": index,
        "queryVector": {"float32": list(vector)},
        "topK": top_k,
        "returnDistance": True,
        "returnMetadata": True,
    }
    if repo:
        body["filter"] = {"repo": {"$eq": repo}}

    response = _oss_vector_request(config, "queryVectors", body)
    vectors = response.get("vectors", [])
    if not isinstance(vectors, list):
        raise IngestError("QueryVectors 响应缺少 vectors 列表")
    return vectors


def list_vectors(
    config: OssConfig, index: str, repo: str | None = None
) -> list[dict[str, Any]]:
    """分页列出索引中的向量（含 metadata）；可按仓库过滤."""
    results: list[dict[str, Any]] = []
    next_token = ""
    seen_tokens: set[str] = set()
    for _ in range(1000):  # 防御异常 nextToken 导致死循环
        body: dict[str, Any] = {
            "indexName": index,
            "maxResults": MAX_LIST_PAGE_SIZE,
            "returnMetadata": True,
        }
        if repo:
            body["filter"] = {"repo": {"$eq": repo}}
        if next_token:
            body["nextToken"] = next_token

        response = _oss_vector_request(config, "listVectors", body)
        page = response.get("vectors", [])
        if not isinstance(page, list):
            raise IngestError("ListVectors 响应缺少 vectors 列表")
        results.extend(page)

        next_token = str(response.get("nextToken") or "")
        if not next_token:
            return results
        if next_token in seen_tokens:
            raise IngestError("ListVectors 返回了重复的 nextToken，已中止")
        seen_tokens.add(next_token)
    raise IngestError("ListVectors 分页次数超过上限，已中止")


def list_repo_counts(config: OssConfig, index: str) -> dict[str, int]:
    """统计各仓库的向量数量；缺少 repo metadata 的归入 (unassigned)."""
    counts: dict[str, int] = {}
    for vector in list_vectors(config, index):
        metadata = vector.get("metadata")
        repo = ""
        if isinstance(metadata, dict):
            value = metadata.get("repo")
            if isinstance(value, str):
                repo = value
        key = repo or "(unassigned)"
        counts[key] = counts.get(key, 0) + 1
    return counts


def delete_vectors(config: OssConfig, index: str, keys: Sequence[str]) -> int:
    """按主键批量删除向量（单批最多 500 条）."""
    unique_keys = list(dict.fromkeys(key for key in keys if key))
    for start in range(0, len(unique_keys), MAX_DELETE_BATCH):
        batch = unique_keys[start : start + MAX_DELETE_BATCH]
        _oss_vector_request(
            config, "deleteVectors", {"indexName": index, "keys": batch}
        )
    return len(unique_keys)


def delete_repo(config: OssConfig, index: str, repo: str, dry_run: bool = False) -> int:
    """删除指定仓库的全部向量；``dry_run`` 只统计不删除."""
    vectors = list_vectors(config, index, repo=repo)
    keys = [
        vector["key"]
        for vector in vectors
        if isinstance(vector.get("key"), str) and vector["key"]
    ]
    if dry_run:
        return len(keys)
    return delete_vectors(config, index, keys)


def embed_texts(options: IngestOptions, texts: Sequence[str]) -> list[list[float]]:
    """调用百炼 ``text-embedding-v4`` 生成向量，返回与输入同序的向量列表."""
    if not texts:
        return []
    if len(texts) > MAX_EMBED_BATCH_SIZE:
        raise IngestError(f"单批文本数 {len(texts)} 超过上限 {MAX_EMBED_BATCH_SIZE}")

    payload = json.dumps(
        {
            "model": options.model,
            "input": {"texts": list(texts)},
            "parameters": {
                "dimension": options.dimension,
                "output_type": "dense",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        DASHSCOPE_EMBED_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {options.dashscope_api_key}",
            "Content-Type": "application/json",
        },
    )
    raw = _http_request(request, options.oss.timeout, options.oss.retries)
    try:
        response = json.loads(raw)
        items = response["output"]["embeddings"]
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
        vectors = [item["embedding"] for item in ordered]
    except (KeyError, TypeError, ValueError) as error:
        preview = raw.decode("utf-8", errors="replace")[:500]
        raise IngestError(f"百炼响应解析失败: {error}; 响应: {preview}") from error

    if len(vectors) != len(texts):
        raise IngestError(
            f"百炼返回向量数 {len(vectors)} 与输入文本数 {len(texts)} 不一致"
        )
    for vector in vectors:
        if len(vector) != options.dimension:
            raise IngestError(
                f"向量维度 {len(vector)} 与配置的 {options.dimension} 不一致"
            )
    return vectors


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按字符窗口切分文本，优先在段落/句子边界断开，并保留少量重叠."""
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return []
    if chunk_size <= 0:
        raise IngestError("chunk_size 必须大于 0")
    overlap = max(0, min(overlap, chunk_size // 2))

    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    total = len(normalized)
    while start < total:
        end = min(start + chunk_size, total)
        if end < total:
            window = normalized[start:end]
            cut = max(window.rfind(boundary) for boundary in _SENTENCE_BOUNDARIES)
            # 边界过靠前时宁可按窗口硬切，避免产生过短的碎片。
            if cut >= chunk_size // 2:
                end = (
                    start
                    + cut
                    + len(
                        next(
                            boundary
                            for boundary in _SENTENCE_BOUNDARIES
                            if window[cut:].startswith(boundary)
                        )
                    )
                )
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= total:
            break
        next_start = max(start + 1, end - overlap)
        newline = normalized.find("\n", next_start, end)
        if newline != -1:
            next_start = newline + 1
        start = next_start
    return chunks


def _iter_input_files(inputs: Sequence[str]) -> Iterator[Path]:
    """展开输入路径：目录递归收集受支持的文本文件，文件原样返回."""
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    yield child
        elif path.is_file():
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise IngestError(
                    f"不支持的文件类型: {path}（支持 "
                    f"{', '.join(sorted(SUPPORTED_SUFFIXES))}）"
                )
            yield path
        else:
            raise IngestError(f"输入路径不存在: {path}")


def _source_label(path: Path) -> str:
    """返回稳定的来源标识：优先使用相对当前目录的路径."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _repo_key_prefix(prefix: str, repo: str) -> str:
    """返回仓库级主键命名空间：不同仓库的同名文件不会互相覆盖."""
    digest = repo_hash(repo)
    return f"{prefix}:{digest}" if prefix else digest


def _default_key(prefix: str, repo: str, source: str, index: int) -> str:
    """生成稳定主键：同一仓库、同一来源的第 N 片始终覆盖同一向量."""
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"{_repo_key_prefix(prefix, repo)}:{source_digest}:{index:04d}"


def _base_metadata(options: IngestOptions, source: str) -> dict[str, Any]:
    """构造所有切片共享的元数据（含仓库与分组，用于检索隔离）."""
    metadata: dict[str, Any] = {"source": source, "kind": options.kind}
    if options.repo:
        metadata["repo"] = options.repo
    if options.group:
        metadata["group"] = options.group
    if options.tags:
        metadata["tags"] = list(options.tags)
    return metadata


def _chunks_from_text(path: Path, options: IngestOptions) -> list[Chunk]:
    """把纯文本/Markdown 文件切成带元数据的切片."""
    text = path.read_text(encoding="utf-8", errors="replace")
    source = _source_label(path)
    parts = chunk_text(text, options.chunk_size, options.chunk_overlap)
    title = next(
        (line[2:].strip() for line in text.splitlines() if line.startswith("# ")),
        "",
    )
    chunks: list[Chunk] = []
    for index, part in enumerate(parts):
        metadata = _base_metadata(options, source)
        metadata.update({"chunk": index, "total": len(parts)})
        if title:
            metadata["title"] = title
        chunks.append(
            Chunk(
                key=_default_key(options.key_prefix, options.repo, source, index),
                text=part,
                metadata=metadata,
            )
        )
    return chunks


def _chunks_from_jsonl(path: Path, options: IngestOptions) -> list[Chunk]:
    """把 JSONL 每行一条记录转换为切片；支持自定义 key/metadata."""
    source = _source_label(path)
    records: list[tuple[dict[str, Any], str]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise IngestError(
                    f"{path}:{line_number} JSON 解析失败: {error}"
                ) from error
            if not isinstance(record, dict):
                raise IngestError(f"{path}:{line_number} 记录必须是 JSON 对象")
            text = record.get("text") or record.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            records.append((record, text.strip()))

    chunks: list[Chunk] = []
    for index, (record, text) in enumerate(records):
        metadata = _base_metadata(options, source)
        extra = record.get("metadata")
        if isinstance(extra, dict):
            metadata.update(extra)
        metadata.update({"chunk": index, "total": len(records)})
        key = record.get("key")
        if not isinstance(key, str) or not key.strip():
            key = _default_key(options.key_prefix, options.repo, source, index)
        else:
            # 自定义 key 也强制落在仓库命名空间内，避免跨仓库覆盖。
            repo_prefix = _repo_key_prefix(options.key_prefix, options.repo)
            key = key.strip()
            if not key.startswith(f"{repo_prefix}:"):
                key = f"{repo_prefix}:{key}"
        chunks.append(Chunk(key=key, text=text, metadata=metadata))
    return chunks


def iter_chunks(inputs: Sequence[str], options: IngestOptions) -> Iterator[Chunk]:
    """遍历所有输入文件并产出切片."""
    for path in _iter_input_files(inputs):
        if path.suffix.lower() == ".jsonl":
            yield from _chunks_from_jsonl(path, options)
        else:
            yield from _chunks_from_text(path, options)


def _vector_payload(chunk: Chunk, embedding: Sequence[float]) -> dict[str, Any]:
    """把切片与向量组装为 PutVectors 的单条记录."""
    return {
        "key": chunk.key,
        "data": {"float32": list(embedding)},
        "metadata": chunk.metadata,
    }


def _chunk_batches(chunks: Sequence[Chunk], size: int) -> Iterator[Sequence[Chunk]]:
    """按固定大小分批，控制单次嵌入请求的文本数量."""
    for start in range(0, len(chunks), size):
        yield chunks[start : start + size]


def run_ingest(inputs: Sequence[str], options: IngestOptions) -> tuple[int, int]:
    """执行切片、向量化与写入，返回（切片数, 写入向量数）."""
    chunks = list(iter_chunks(inputs, options))
    if not chunks:
        raise IngestError("没有找到可写入的文本内容")
    if options.dry_run:
        for chunk in chunks:
            print(
                f"[dry-run] {chunk.key}\t{len(chunk.text)} chars\t"
                f"{chunk.metadata.get('source', '')}"
            )
        return len(chunks), 0

    written = 0
    pending: list[dict[str, Any]] = []
    for batch in _chunk_batches(chunks, options.embed_batch_size):
        embeddings = embed_texts(options, [chunk.text for chunk in batch])
        pending.extend(
            _vector_payload(chunk, embedding)
            for chunk, embedding in zip(batch, embeddings)
        )
        while len(pending) >= options.vector_batch_size:
            put_vectors(
                options.oss, options.oss.index, pending[: options.vector_batch_size]
            )
            written += options.vector_batch_size
            pending = pending[options.vector_batch_size :]
            if not options.quiet:
                print(f"[write] 已写入 {written}/{len(chunks)}", file=sys.stderr)

    if pending:
        put_vectors(options.oss, options.oss.index, pending)
        written += len(pending)
        if not options.quiet:
            print(f"[write] 已写入 {written}/{len(chunks)}", file=sys.stderr)
    return len(chunks), written


def run_query(
    options: IngestOptions,
    query: str,
    top_k: int,
    as_json: bool = False,
    all_repos: bool = False,
) -> int:
    """检索知识库并输出结果，返回命中条数.

    默认只检索当前仓库；``all_repos=True`` 时跨仓库检索。
    """
    embedding = embed_texts(options, [query])[0]
    repo = None if all_repos else (options.repo or None)
    results = query_vectors(options.oss, options.oss.index, embedding, top_k, repo)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return len(results)

    if not results:
        print("未找到匹配结果", file=sys.stderr)
        return 0

    scope = "all repos" if all_repos else (options.repo or "unknown")
    print(f"scope: {scope}", file=sys.stderr)
    for index, item in enumerate(results, start=1):
        distance = item.get("distance")
        print(f"{index}. {item.get('key', '')}  distance={distance}")
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            for field_name in ("repo", "source", "title", "kind", "chunk", "tags"):
                if field_name in metadata:
                    print(f"   {field_name}: {metadata[field_name]}")
    return len(results)


def _env(*names: str) -> str:
    """返回第一个非空环境变量的值."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _build_options(args: argparse.Namespace) -> IngestOptions:
    """把命令行参数转换为导入配置，并做基础校验."""
    access_key_id = _env("ALIBABA_CLOUD_ACCESS_KEY_ID", "OSS_ACCESS_KEY_ID")
    access_key_secret = _env("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "OSS_ACCESS_KEY_SECRET")
    endpoint = args.endpoint or (
        f"{args.region}-internal.oss-vectors.aliyuncs.com"
        if args.internal
        else f"{args.region}.oss-vectors.aliyuncs.com"
    )

    list_mode = bool(getattr(args, "list_repos", False))
    delete_mode = bool(getattr(args, "delete", False))
    if list_mode and (
        delete_mode or args.query or args.repo or args.all_repos or args.inputs
    ):
        raise IngestError("--list-repos 不能与其它操作或输入混用")
    if delete_mode and (list_mode or args.query or args.all_repos or args.inputs):
        raise IngestError("--delete 只能与 --repo 搭配使用")
    if delete_mode and not args.repo:
        raise IngestError("--delete 必须显式指定 --repo，避免误删")

    needs_oss = list_mode or delete_mode or not args.dry_run
    needs_dashscope = not (list_mode or delete_mode) and not args.dry_run
    if needs_oss:
        missing = [
            name
            for name, value in (
                ("ALIBABA_CLOUD_ACCESS_KEY_ID", access_key_id),
                ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", access_key_secret),
            )
            if not value
        ]
        if needs_dashscope and not _env("DASHSCOPE_API_KEY"):
            missing.append("DASHSCOPE_API_KEY")
        if not args.account_id:
            missing.append("--account-id / OSS_VECTOR_ACCOUNT_ID")
        if missing:
            raise IngestError("缺少必要配置: " + ", ".join(missing))

    if not 0 < args.embed_batch_size <= MAX_EMBED_BATCH_SIZE:
        raise IngestError(f"embed_batch_size 必须在 1~{MAX_EMBED_BATCH_SIZE} 之间")
    if not 0 < args.vector_batch_size <= MAX_VECTOR_BATCH_SIZE:
        raise IngestError(f"vector_batch_size 必须在 1~{MAX_VECTOR_BATCH_SIZE} 之间")
    if args.chunk_size <= 0:
        raise IngestError("chunk_size 必须大于 0")
    if args.chunk_overlap < 0 or args.chunk_overlap >= args.chunk_size:
        raise IngestError("chunk_overlap 必须满足 0 <= overlap < chunk_size")
    if args.dimension <= 0:
        raise IngestError("dimension 必须大于 0")
    if args.timeout <= 0:
        raise IngestError("timeout 必须大于 0")
    if args.retries < 0:
        raise IngestError("retries 不能为负数")

    repo: str | None
    if list_mode:
        repo = ""
    elif delete_mode:
        repo = normalize_repo(args.repo)
        if not repo:
            raise IngestError(f"--repo 非法: {args.repo!r}")
    elif args.all_repos:
        if args.repo:
            raise IngestError("--all-repos 与 --repo 不能同时使用")
        if not args.query:
            raise IngestError("--all-repos 仅用于检索，写入必须指定仓库")
        repo = None
    elif args.repo:
        repo = normalize_repo(args.repo)
        if not repo:
            raise IngestError(f"--repo 非法: {args.repo!r}")
    else:
        repo = detect_repo()
        if not repo:
            raise IngestError(
                "无法确定仓库，请使用 --repo 指定（检索时可改用 --all-repos）"
            )

    group = repo_group(repo) if repo else ""
    oss = OssConfig(
        bucket=args.bucket,
        index=args.index,
        region=args.region,
        account_id=args.account_id,
        endpoint=endpoint,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        timeout=args.timeout,
        retries=args.retries,
    )
    return IngestOptions(
        oss=oss,
        dashscope_api_key=_env("DASHSCOPE_API_KEY"),
        repo=repo or "",
        group=group,
        model=args.model,
        dimension=args.dimension,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        embed_batch_size=args.embed_batch_size,
        vector_batch_size=args.vector_batch_size,
        key_prefix=args.key_prefix,
        kind=args.kind,
        tags=args.tag,
        dry_run=args.dry_run,
        quiet=args.quiet,
    )


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器."""
    parser = argparse.ArgumentParser(
        description=(
            "将知识文档切片、调用百炼 text-embedding-v4 生成向量，"
            "并通过 OSS V4 签名 API 按仓库隔离地写入/检索/审计/清理向量 Bucket"
            "（无需 SDK/ossutil）。"
        )
    )
    parser.add_argument(
        "inputs", nargs="*", help="Markdown/TXT 文件、JSONL 或目录（写入模式）"
    )
    parser.add_argument("--query", default="", help="检索文本；提供后进入只读检索模式")
    parser.add_argument("--top-k", type=int, default=5, help="检索返回条数（上限 500）")
    parser.add_argument("--json", action="store_true", help="检索/审计结果以 JSON 输出")
    parser.add_argument(
        "--list-repos",
        action="store_true",
        help="列出各仓库的向量数量（仓库级审计）",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="删除 --repo 指定仓库的全部向量；建议先加 --dry-run 预览",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="仓库标识（owner/repo 或 org/group/repo），默认从 origin 自动识别",
    )
    parser.add_argument(
        "--all-repos",
        action="store_true",
        help="跨仓库检索；写入模式必须指定具体仓库",
    )
    parser.add_argument(
        "--bucket",
        default=_env("OSS_VECTOR_BUCKET") or "cnb-knowledge-base",
        help="向量 Bucket 名称",
    )
    parser.add_argument(
        "--index",
        default=_env("OSS_VECTOR_INDEX") or "cnbknowledge",
        help="向量索引名称",
    )
    parser.add_argument(
        "--region",
        default=_env("OSS_VECTOR_REGION") or "cn-shanghai",
        help="OSS 地域 ID",
    )
    parser.add_argument(
        "--account-id",
        default=_env("OSS_VECTOR_ACCOUNT_ID"),
        help="阿里云账号 ID（可通过 OSS_VECTOR_ACCOUNT_ID 设置）",
    )
    parser.add_argument(
        "--endpoint",
        default=_env("OSS_VECTOR_ENDPOINT"),
        help="向量 API 域名后缀，默认 <region>.oss-vectors.aliyuncs.com",
    )
    parser.add_argument(
        "--internal",
        action="store_true",
        help="使用内网 Endpoint（<region>-internal.oss-vectors.aliyuncs.com）",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="嵌入模型名称")
    parser.add_argument(
        "--dimension", type=int, default=DEFAULT_DIMENSION, help="向量维度"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="单切片最大字符数",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="相邻切片重叠字符数",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=DEFAULT_EMBED_BATCH_SIZE,
        help="单次嵌入请求的文本数（上限 10）",
    )
    parser.add_argument(
        "--vector-batch-size",
        type=int,
        default=DEFAULT_VECTOR_BATCH_SIZE,
        help="单次 PutVectors 的向量数（上限 500）",
    )
    parser.add_argument(
        "--key-prefix", default="cnb", help="向量主键前缀，留空表示不加前缀"
    )
    parser.add_argument(
        "--kind", default="knowledge", help="元数据 kind 字段（如 knowledge/pitfall）"
    )
    parser.add_argument(
        "--tag", action="append", default=[], help="元数据标签，可重复传入"
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP 超时秒数")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument(
        "--dry-run", action="store_true", help="只展示切片计划，不调用任何 API"
    )
    parser.add_argument("--quiet", action="store_true", help="减少进度输出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """脚本入口：解析参数并执行导入、检索、审计或清理."""
    args = build_parser().parse_args(argv)
    try:
        if args.query and args.inputs:
            raise IngestError("--query 与输入文件不能同时使用")
        if args.query and args.dry_run:
            raise IngestError("--query 不支持 --dry-run")
        if not (args.query or args.list_repos or args.delete) and not args.inputs:
            raise IngestError(
                "请提供输入文件，或使用 --query / --list-repos / --delete"
            )

        options = _build_options(args)
        if args.list_repos:
            counts = list_repo_counts(options.oss, options.oss.index)
            if args.json:
                print(json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                total = 0
                for repo, count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                ):
                    print(f"{count:>6}  {repo}")
                    total += count
                print(f"{total:>6}  TOTAL")
            return 0

        if args.delete:
            count = delete_repo(
                options.oss, options.oss.index, options.repo, args.dry_run
            )
            if args.dry_run:
                print(
                    f"[dry-run] 将删除 {count} 条向量（repo={options.repo}）",
                    file=sys.stderr,
                )
            else:
                print(
                    f"已删除 {count} 条向量（repo={options.repo}）",
                    file=sys.stderr,
                )
            return 0

        if args.query:
            count = run_query(
                options, args.query, args.top_k, args.json, args.all_repos
            )
            if not args.quiet and not args.json:
                print(f"查询完成：返回 {count} 条结果", file=sys.stderr)
            return 0

        chunk_count, written = run_ingest(args.inputs, options)
    except IngestError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130

    if options.dry_run:
        print(f"dry-run 完成：{chunk_count} 个切片（未写入）", file=sys.stderr)
    else:
        print(
            f"完成：{chunk_count} 个切片，写入 {written} 条向量到 "
            f"{options.oss.bucket}/{options.oss.index}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

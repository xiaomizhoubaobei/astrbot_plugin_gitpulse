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
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".jsonl"})
# 切片时优先在这些边界处断开，避免把句子/段落从中间截断。
_SENTENCE_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", ". ", "! ", "? ")


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
    """发送 HTTP 请求；对限流/5xx 做指数退避重试，其余错误直接抛出."""
    last_error = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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


def put_vectors(
    config: OssConfig, index: str, vectors: Sequence[dict[str, Any]]
) -> None:
    """调用 ``PutVectors`` 写入一批向量（单批最多 500 条）."""
    if not vectors:
        return
    if len(vectors) > MAX_VECTOR_BATCH_SIZE:
        raise IngestError(f"单批向量数 {len(vectors)} 超过上限 {MAX_VECTOR_BATCH_SIZE}")

    body = json.dumps(
        {"indexName": index, "vectors": list(vectors)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    # OSS 要求 Content-MD5；这里仅做传输校验，不用于安全目的。
    content_md5 = base64.b64encode(
        hashlib.md5(body, usedforsecurity=False).digest()
    ).decode("ascii")
    timestamp = _now_timestamp()
    canonical_uri = f"/{_uri_encode(config.bucket_arn, keep_slash=True)}/"
    headers = {
        "content-md5": content_md5,
        "content-type": "application/json",
        "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
        "x-oss-date": timestamp,
    }
    authorization = sign_v4(
        method="POST",
        canonical_uri=canonical_uri,
        canonical_query="putVectors",
        headers=headers,
        access_key_id=config.access_key_id,
        access_key_secret=config.access_key_secret,
        region=config.region,
        timestamp=timestamp,
    )
    request = urllib.request.Request(
        f"https://{config.host}/?putVectors",
        data=body,
        method="POST",
        headers={
            "Content-MD5": content_md5,
            "Content-Type": "application/json",
            "x-oss-content-sha256": "UNSIGNED-PAYLOAD",
            "x-oss-date": timestamp,
            "Authorization": authorization,
            "User-Agent": "gitpulse-vector-ingest/1.0",
        },
    )
    _http_request(request, config.timeout, config.retries)


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


def _default_key(prefix: str, source: str, index: int) -> str:
    """生成稳定主键：同一来源的第 N 片始终覆盖同一向量."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    base = f"{digest}:{index:04d}"
    return f"{prefix}:{base}" if prefix else base


def _base_metadata(options: IngestOptions, source: str) -> dict[str, Any]:
    """构造所有切片共享的元数据."""
    metadata: dict[str, Any] = {"source": source, "kind": options.kind}
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
                key=_default_key(options.key_prefix, source, index),
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
            key = _default_key(options.key_prefix, source, index)
        chunks.append(Chunk(key=key.strip(), text=text, metadata=metadata))
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
    if not args.dry_run:
        missing = [
            name
            for name, value in (
                ("ALIBABA_CLOUD_ACCESS_KEY_ID", access_key_id),
                ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", access_key_secret),
                ("DASHSCOPE_API_KEY", _env("DASHSCOPE_API_KEY")),
            )
            if not value
        ]
        if not args.account_id:
            missing.append("--account-id / OSS_VECTOR_ACCOUNT_ID")
        if missing:
            raise IngestError("缺少必要配置: " + ", ".join(missing))

    if not args.dry_run and args.embed_batch_size > MAX_EMBED_BATCH_SIZE:
        raise IngestError(f"embed_batch_size 最大为 {MAX_EMBED_BATCH_SIZE}")
    if not args.dry_run and args.vector_batch_size > MAX_VECTOR_BATCH_SIZE:
        raise IngestError(f"vector_batch_size 最大为 {MAX_VECTOR_BATCH_SIZE}")
    if args.chunk_size <= 0:
        raise IngestError("chunk_size 必须大于 0")
    if args.chunk_overlap < 0 or args.chunk_overlap >= args.chunk_size:
        raise IngestError("chunk_overlap 必须满足 0 <= overlap < chunk_size")

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
            "并通过 OSS V4 签名 API 写入向量 Bucket（无需 SDK/ossutil）。"
        )
    )
    parser.add_argument("inputs", nargs="+", help="Markdown/TXT 文件、JSONL 或目录")
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
    """脚本入口：解析参数并执行导入."""
    args = build_parser().parse_args(argv)
    try:
        options = _build_options(args)
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

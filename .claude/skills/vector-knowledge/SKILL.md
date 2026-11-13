---
name: vector-knowledge
description: 读写阿里云 OSS 向量知识库。任务开始召回跨仓库结论、收尾沉淀踩坑经验、检索或导入知识文档时使用。
---

# 向量知识库

阿里云上海 OSS 向量 Bucket 是跨仓库共用的语义知识池，替代原百炼共享记忆库。只存结论性知识，不存任务流水、日志或密钥。

## 连接信息

- Bucket：`cnb-knowledge-base`
- 索引：`cnbknowledge`（`cn-shanghai`，1024 维 float32，cosine）
- 工具：`scripts/vector_ingest.py`（纯标准库，自实现百炼 embedding 与 OSS V4 签名）

## 何时用

- **任务开始**：先检索，命中优先复用；空结果属正常，不重试。
- **任务收尾**：仅当产出跨仓库可复用结论（平台约定、踩坑解法、字段口径）时写入。
- **不要用**：一次性任务、只依赖当前上下文、未脱敏的敏感信息。

## 命令

```bash
# 检索
python scripts/vector_ingest.py --query "<关键词或报错原文>" --top-k 5

# 写入单个文件或目录（--kind / --tag 可选）
python scripts/vector_ingest.py --kind pitfall --tag cnb docs/05-troubleshooting.md
```

环境变量：`DASHSCOPE_API_KEY`、`ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、`OSS_VECTOR_ACCOUNT_ID`。

## 纪律

1. 同一来源重复写入会覆盖同序号切片；先检索查重，避免重复内容。
2. 写入内容写清「结论 + 适用场景」，一条切片只讲一件事。
3. 写入后索引有约数十秒延迟，刚写入的内容可能暂时检索不到。
4. 知识库是增强能力：调用失败只告警，不阻断主任务。
5. 详细参数、批量导入与故障排查见 `scripts/README.md`。
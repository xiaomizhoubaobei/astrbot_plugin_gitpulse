---
name: bailian-memory
description: 读写组织百炼共享记忆库。任务开始召回历史结论、收尾沉淀跨仓库可复用经验、纠偏或退役过期记忆时使用。
---

# 百炼记忆库

本组织所有仓库共用的语义知识池，按 `user_id`（触发者登录名，跨仓库统一）归档。只存结论性知识，不存任务流水、日志或密钥。

## 何时用

- **任务开始**且可能受益于历史结论：`search`（阈值 0.5~0.7），命中优先复用；空结果属正常。
- **任务收尾**且产出跨仓库可复用结论：`add`；同一结论已存在则 `update`。
- **结论过时**：先 `search` / `list` 拿到 `memory_node_id` 再 `update`；彻底失效才 `delete`。
- **不要用**：一次性任务、答案只依赖当前上下文、涉及未脱敏敏感信息。

## 连接信息

- Base：`https://dashscope.aliyuncs.com/api/v2/apps/memory`
- 鉴权：`Authorization: Bearer $DASHSCOPE_API_KEY`（缺失时跳过记忆环节，不阻断任务）
- `memory_library_id`：`22fcd3f37eee42d6ac99cf25d03ac6c3`
- `user_id`：优先 `$MEMORY_USER_ID`，否则 `$CNB_BUILD_USER`（如 `qixiaoxin`）；不要拼组织或仓库名

```bash
BASE="${MEMORY_API_BASE_URL:-https://dashscope.aliyuncs.com/api/v2/apps/memory}"
USER_ID="${MEMORY_USER_ID:-${CNB_BUILD_USER:-}}"
LIB="${MEMORY_LIBRARY_ID:-22fcd3f37eee42d6ac99cf25d03ac6c3}"
AUTH="Authorization: Bearer $DASHSCOPE_API_KEY"
```

## 优先调用运行时脚本

镜像内存在 `/app/scripts/bailian-memory.py` 时优先使用（参数与元数据推导更省心）：

```bash
python3 /app/scripts/bailian-memory.py search --query "GPG 签名 unknown_key"
python3 /app/scripts/bailian-memory.py add --content "结论 + 适用场景"
python3 /app/scripts/bailian-memory.py list
python3 /app/scripts/bailian-memory.py update --node-id <memory_node_id> --content "修正后的结论"
python3 /app/scripts/bailian-memory.py delete --node-id <memory_node_id>
```

**禁止**把该脚本复制进本仓库：它属于 SuperNPC 运行时，复制会造成版本分叉。

## HTTP API 兜底（开发容器无运行时脚本时）

参数位置有坑：`search` 必须用 `messages` 而非 `query`；`DELETE` 的 `user_id` / `memory_library_id` 放 query，`PATCH` 放 body。

### 检索

```bash
curl -sS -X POST "$BASE/memory_nodes/search" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"memory_library_id\":\"$LIB\",\"messages\":[{\"role\":\"user\",\"content\":\"$QUERY\"}],\"max_results\":5,\"similarity_threshold\":0.5}"
```

### 新增

```bash
curl -sS -X POST "$BASE/add" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"memory_library_id\":\"$LIB\",\"custom_content\":\"$CONTENT\",\"meta_data\":{\"repo_slug\":\"$REPO_SLUG\",\"repo_scope\":\"$REPO_SCOPE\",\"user_identity\":\"$USER_ID\"}}"
```

### 列出

```bash
curl -sS "$BASE/memory_nodes?user_id=$USER_ID&memory_library_id=$LIB&page_size=20&page_num=1" -H "$AUTH"
```

### 更新 / 删除

```bash
curl -sS -X PATCH "$BASE/memory_nodes/$NODE_ID" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"memory_library_id\":\"$LIB\",\"custom_content\":\"$CONTENT\"}"

curl -sS -X DELETE "$BASE/memory_nodes/$NODE_ID?user_id=$USER_ID&memory_library_id=$LIB" -H "$AUTH"
```

## 写入规范

- 一次只写一条结论，≤512 字符，讲清「结论 + 适用场景」；不要把整段对话灌入。
- `add` 前先 `search` 查重；已存在用 `update`，避免近似片段污染召回。
- `delete` 不可恢复，删除前必须确认 `memory_node_id`。
- 记忆调用失败只告警并继续任务，绝不阻断主流程。

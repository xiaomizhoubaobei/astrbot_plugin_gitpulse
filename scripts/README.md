# GitPulse 脚本工具

这个目录包含测试 GitPulse 插件与维护项目知识库的脚本。

## vector_ingest.py

把 Markdown / 文本 / JSONL 知识文档切片，调用百炼 `text-embedding-v4` 生成向量，
再通过 **OSS V4 签名 API** 写入阿里云 OSS 向量 Bucket；也可用 `--query` 做语义检索。

脚本只依赖 Python 标准库：运行镜像未安装 OSS / 百炼 SDK 时也能直接执行，
V4 签名（`OSS4-HMAC-SHA256`）由脚本内部实现。

### 前提

1. 已创建 OSS 向量 Bucket 与索引，且索引维度与 `--dimension` 一致；
2. 环境变量已注入凭证（不会写入命令行，避免出现在进程列表）：

```bash
export DASHSCOPE_API_KEY="sk-..."                       # 百炼嵌入模型
export ALIBABA_CLOUD_ACCESS_KEY_ID="..."                # OSS V4 签名
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="..."
export OSS_VECTOR_ACCOUNT_ID="<阿里云账号 ID>"           # 也可用 --account-id
```

可选环境变量：`OSS_VECTOR_BUCKET`、`OSS_VECTOR_INDEX`、`OSS_VECTOR_REGION`、
`OSS_VECTOR_ENDPOINT`；命令行参数优先级更高。

### 基本用法

```bash
# 1. 先看切片计划，不调用任何 API、不消耗额度
./scripts/vector_ingest.py --dry-run docs/

# 2. 导入单个 Markdown / 文本文件
./scripts/vector_ingest.py --kind pitfall --tag cnb --tag gpg docs/05-troubleshooting.md

# 3. 递归导入目录
./scripts/vector_ingest.py --kind knowledge --tag cnb docs/

# 4. 使用内网 Endpoint（与 OSS 同地域的 ECS 上运行时）
./scripts/vector_ingest.py --internal docs/

# 5. 自定义切片大小与批次
./scripts/vector_ingest.py \
  --chunk-size 800 --chunk-overlap 100 \
  --embed-batch-size 10 --vector-batch-size 100 \
  docs/
```

### 仓库隔离

每个仓库的知识通过 `repo` metadata 隔离，检索时由服务端过滤，不会召回其他仓库的内容：

- `--repo` 未传时自动从 `git remote get-url origin` 识别；
- 写入时主键为 `cnb:<仓库哈希>:<来源哈希>:<切片号>`，不同仓库的同名文件不会互相覆盖；
- 元数据写入 `repo` 与 `group`（CNB 三级路径取第二段，GitHub 两级路径取 owner）；
- 检索默认只查当前仓库，`--all-repos` 可跨仓库检索（仅检索模式）；
- **当前知识库只收纳 `XMZZUZHI/Astrbot/*` 子组织**；其他分组的数据已清理，不再写入。

```bash
# 显式指定仓库（自动识别失败时使用）
./scripts/vector_ingest.py \
  --repo XMZZUZHI/Astrbot/astrbot_plugin_github_webhook docs/

# 只检索当前仓库
./scripts/vector_ingest.py \
  --repo XMZZUZHI/Astrbot/astrbot_plugin_github_webhook \
  --query "GPG unknown_key" --top-k 5

# 跨仓库检索
./scripts/vector_ingest.py --all-repos --query "GPG unknown_key" --top-k 5
```

> 组织仓库数量已超过 OSS 单桶 100 个索引的上限，因此不要按仓库建索引；
> 统一使用 metadata 隔离，未来需要物理拆分时可按仓库哈希迁移。

### 检索

```bash
# 语义检索，输出 key、distance 与 repo/source/title/kind 等元数据
./scripts/vector_ingest.py --query "GPG 签名 unknown_key 怎么修" --top-k 5

# 机器可读输出
./scripts/vector_ingest.py --query "端口被占用" --top-k 3 --json
```

> 向量写入后索引更新有短暂延迟（约数十秒），刚写入的内容可能暂时检索不到。
> 检索模式与写入模式互斥，不能同时传 `--query` 和输入文件。

### 输入格式

- `.md` / `.markdown` / `.txt`：按 `--chunk-size` 切片，优先在段落/句子边界断开，
  保留 `--chunk-overlap` 字符重叠；Markdown 首个 `# ` 标题会写入 metadata。
- `.jsonl`：每行一个 JSON 对象，支持以下字段：

```json
{"text": "正文", "metadata": {"repo": "owner/repo"}, "key": "可选自定义主键"}
{"content": "与 text 等价", "metadata": {"kind": "pitfall"}}
```

### 主键与幂等

文本文件的向量主键为 `cnb:<仓库哈希>:<来源路径哈希>:<切片序号>`。对同一仓库、
同一来源重复导入会**覆盖**相同序号的向量；若文件变短导致切片数减少，旧的多余
切片不会自动删除，需要按主键另行清理。JSONL 的 `key` 字段会被自动加上仓库
命名空间前缀，避免跨仓库覆盖。

### 限制

- 单次嵌入请求最多 10 条文本（脚本会分批，超过上限直接报错）；
- 单次 `PutVectors` 最多 500 条向量；
- 单次 `QueryVectors` 的 `--top-k` 范围为 1~500；
- `text-embedding-v4` 的 `dimension` 必须与索引维度一致（默认 1024）；
- 网络失败、429/5xx 会按指数退避重试，其余错误直接退出并打印响应体。

### 测试

```bash
python -m pytest tests/test_vector_ingest.py -q
```

## test-webhook.sh

一个集成的测试脚本，支持通过命令行参数选择不同的事件类型。

### 功能

- 支持 push、issues、pull_request、release、workflow_run、star、watch、issue_comment、ping 事件类型
- 可自定义服务器地址、端口与 Webhook 路径
- 支持 webhook 密钥签名
- 详细的输出模式

### 使用方法

#### 基本用法

```bash
# 发送 push 事件到默认服务器 (localhost:8080)
./test-webhook.sh push

# 发送 Pull Request 事件
./test-webhook.sh pr

# 发送 Issue 事件
./test-webhook.sh issues

# 发送 Release / Workflow Run / Star / 评论事件
./test-webhook.sh release
./test-webhook.sh workflow_run
./test-webhook.sh star
./test-webhook.sh comment

# 发送 Ping 事件（健康检查）
./test-webhook.sh ping
```

#### 高级用法

```bash
# 指定端口
./test-webhook.sh -p 6100 push

# 使用 webhook 密钥
./test-webhook.sh -s "your-secret-here" issues

# 指定服务器地址
./test-webhook.sh -H 192.168.1.100 pr

# 指定自定义 Webhook 路径（需与插件 webhook_path 一致）
./test-webhook.sh -P /github/webhook ping

# 详细输出模式（显示完整的 payload）
./test-webhook.sh -v push

# 组合参数
./test-webhook.sh -H localhost -p 6100 -s "secret" -v push
```

#### 参数说明

| 参数 | 长参数 | 说明 | 默认值 |
|------|--------|------|--------|
| `-H` | `--host` | 服务器地址 | localhost |
| `-p` | `--port` | 服务器端口 | 8080 |
| `-P` | `--path` | Webhook 路径 | /webhook |
| `-s` | `--secret` | Webhook 密钥 | 无 |
| `-v` | `--verbose` | 详细输出 | false |
| `-h` | `--help` | 显示帮助信息 | - |

#### 事件类型

| 事件类型 | 说明 |
|---------|------|
| `push` | 推送事件 - 测试代码推送通知 |
| `issues` | Issue 事件 - 测试 Issue 创建/更新 |
| `pr` | Pull Request 事件 - 测试 PR 创建/更新 |
| `release` | Release 事件 - 测试版本发布通知 |
| `workflow_run` | Workflow Run 事件 - 测试 CI/CD 完成通知（别名 `workflow`） |
| `star` | Star 事件 - 测试新增 Star 通知 |
| `watch` | Watch 事件 - 测试旧版 starred 通知（payload 与 `star` 一致） |
| `comment` | Issue Comment 事件 - 测试 Issue / PR 评论通知 |
| `ping` | Ping 事件 - 测试服务器连接 |

### 测试示例

#### 1. 测试默认配置

```bash
./test-webhook.sh push
```

输出：
```
=== GitHub Webhook 测试 ===
事件类型: push
目标地址: http://localhost:8080/webhook

✓ 请求发送成功

=== 测试完成 ===
```

#### 2. 测试特定端口

如果你的插件配置的端口是 6100：

```bash
./test-webhook.sh -p 6100 issues
```

#### 3. 详细模式查看 payload

```bash
./test-webhook.sh -v pr
```

这将显示完整的 JSON payload：
```json
{
  "action": "opened",
  "number": 10,
  "pull_request": {
    "id": 98765,
    "title": "Test Pull Request",
    ...
  }
}
```

#### 4. 测试 webhook 签名验证

如果你的插件配置了 webhook_secret：

```bash
./test-webhook.sh -s "my-webhook-secret" push
```

脚本会自动计算 HMAC-SHA256 签名并添加到 `X-Hub-Signature-256` 请求头中。

### 注意事项

1. **确保插件已启动**：测试前请确保 AstrBot 和插件已正确启动
2. **端口配置**：使用 `-p` 参数指定正确的端口，需要与插件配置的端口一致
3. **路径配置**：如果插件配置了自定义 `webhook_path`，需用 `-P` 传入相同路径
4. **密钥配置**：如果插件启用了签名验证，必须使用 `-s` 参数提供相同的密钥
5. **防火墙**：如果是远程测试，确保防火墙允许访问该端口

### 故障排查

#### 连接被拒绝

```
✗ 请求失败 (退出码: 7)
```

可能原因：
- 插件未启动
- 端口号错误
- 防火墙阻止连接

解决方法：
```bash
# 检查端口是否被监听
lsof -i :8080
```

#### 签名验证失败

如果启用了签名验证，未提供密钥（缺少 `X-Hub-Signature-256` 头）或签名不匹配的请求都会被拒绝（HTTP 401）。

#### 速率限制

如果插件配置了速率限制，发送过多请求会返回 HTTP 429。

### 自定义 payload

如需使用自定义的 payload，可以修改脚本中的 `PAYLOAD` 变量，或直接使用 curl：

```bash
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{
    "ref": "refs/heads/main",
    "repository": {
      "name": "my-repo"
    }
  }'
```

### 依赖

- `curl` - 用于发送 HTTP 请求
- `jq` - 用于格式化 JSON 输出（可选，详细模式使用）

安装依赖：
```bash
# macOS
brew install curl jq

# Ubuntu/Debian
sudo apt-get install curl jq

# CentOS/RHEL
sudo yum install curl jq
```

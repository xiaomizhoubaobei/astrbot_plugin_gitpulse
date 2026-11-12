# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

GitPulse 是一个 **AstrBot 插件**（Python 3.12+），用于接收 GitHub Webhook（`push` / `issues` / `pull_request` / `release` / `workflow_run` / `star` / `watch` / `issue_comment` / `ping`），格式化为聊天消息后转发到 AstrBot 指定会话；可选通过 AstrBot 的 LLM 能力改写消息。插件不是独立进程——由 AstrBot 加载后，自己启动一个 aiohttp HTTP 服务监听配置路径 `webhook_path`（默认 `/webhook`）。

**`AGENTS.md` 是本仓库的权威工程规范**（提交信息、GPG 强制签名、PR 流程、CI 口径、目录约定），优先级高于本文件；动手前先读相关章节。

## 常用命令

```bash
# 安装依赖（含 pytest/coverage；不安装 AstrBot 宿主也能跑单测）
pip install -r requirements-dev.txt

# 运行全部测试
python -m pytest tests -q

# 运行单个测试文件 / 单个用例
python -m pytest tests/test_pull_request_formatter.py -q
python -m pytest tests/test_pull_request_formatter.py::test_synchronize_action_uses_merge_emoji -q

# 与 CI 完全一致的覆盖率口径（以 src 包为计数源）
python -m coverage run --source=src -m pytest tests -q
python -m coverage report -m
python -m coverage xml -o coverage.xml

# 代码检查（提交前必须执行）
pip install pre-commit
pre-commit install
pre-commit run --all-files

# 手动向已启动的插件发测试 payload（需 AstrBot 已加载插件并在监听）
./scripts/test-webhook.sh push      # 也支持 issues / pr / ping
./scripts/test-webhook.sh -p 6100 -s "your-secret" -v pr
```

说明：

- 运行依赖 `aiohttp` 同时声明在根 `requirements.txt` 与 `src/requirements.txt`，改版本时**两处一起改**（Dependabot 分别跟踪）。
- `tests/conftest.py` 在无法导入 `astrbot` 时会注入最小桩，因此纯净环境也能跑测试；若本机按 `requirements-dev.txt` 注释装了上游宿主，则使用真实宿主。
- CNB 云原生开发容器（`.cnb.yml` 的 vscode 阶段）会把上游 AstrBot 的 `astrbot/` 包复制到仓库根。它会被 Python 当作 `astrbot` 导入，从而**绕过 conftest 的桩**并因缺宿主依赖导致测试/mypy 报错；遇到这种情况，把该目录临时移开（它已在 `.gitignore` 中）或在纯净目录跑测试。

## 架构与数据流

```
main.py: PluginEntry（@register 注册的 AstrBot 入口）
  └─ src/core/plugin.py: GitHubWebhookPlugin
       ├─ aiohttp app，仅 POST <webhook_path>（默认 /webhook）
       ├─ src/utils/rate_limiter.py     滑动窗口限流
       ├─ src/utils/verify_signature.py HMAC-SHA256 验签
       ├─ src/handlers/*                从 payload 提取字段
       ├─ src/formatters/*              纯函数：字段 → 消息文本
       └─ 发送：context.send_message(<repo_routes 解析后的 UMO>)
            ├─ src/services/repo_router.py 解析 repo_routes（精确 → owner/* → * → target_umo）
            └─ enable_agent 时先经 src/services/llm_service.py 改写
```

请求处理顺序（`GitHubWebhookPlugin.handle_webhook`）：

1. 限流（`rate_limit > 0` 时）；
2. 读取并解析 JSON；
3. 验签：只要配置了 `webhook_secret` 就必须携带 `X-Hub-Signature-256`——缺少签名头与签名不匹配均返回 401；仅在未配置 `webhook_secret` 时跳过校验；
4. `X-GitHub-Event` 分发：`ping` 直接回 `Pong`（不受白名单影响）；`SUPPORTED_EVENT_TYPES` 内的事件先过 `enabled_events` 白名单，再交给对应 handler；未支持或未启用的事件只记日志并回 200；
5. 支持且启用的事件在验签与白名单通过后**立即返回 200**，并通过 `asyncio.create_task` 把后续处理转入后台，避免 LLM 处理超过 GitHub 的 10 秒响应超时而触发重复推送；插件卸载时 `terminate` 会取消未完成的后台任务；
6. 后台任务依次调用 handler、解析 `repo_routes`、按 `enable_agent` 走 LLM 或直接发送；处理异常只记日志，不影响已返回的响应。

分层职责：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 入口 | `main.py` | 把仓库根加入 `sys.path`，代理到真正的插件类 |
| 核心 | `src/core/plugin.py` | HTTP 服务、限流、验签、事件路由、发送 |
| 配置 | `src/core/config.py` | `PluginConfig`（`ConfigNode` 子类）强类型读取配置 |
| 常量 | `src/core/constants.py` | 事件类型、动作、默认端口/监听地址 |
| 处理 | `src/handlers/` | payload → formatter 参数 |
| 格式化 | `src/formatters/` | 纯函数，输出消息文本 |
| 工具 | `src/utils/` | 限流器、签名校验 |
| 服务 | `src/services/llm_service.py` | 调 `context.llm_generate`，超时用 `agent_timeout` |

关键约定与易踩点：

- **动作常量必须与 GitHub 真实枚举一致**：PR 同步动作是 `synchronize`（无尾字母 d，见 `ACTION_PR_SYNCHRONIZE`），formatter 的 emoji 映射键一律引用 `src/core/constants.py`，不要写回硬编码字符串，否则会再次出现「🔀 永不命中」。
- **配置缺失兜底**：宿主 `AstrBotConfig` 对缺失键返回 `None`，`ConfigNode` 按字段类型注解给出零值兜底；`bind_host` 是 property（缺省回落 `DEFAULT_BIND_HOST`），不要改成普通字段。
- **默认监听 `0.0.0.0`** 是有意为之（GitHub 需从外部访问），`src/core/constants.py` 中带 `# nosec B104`；暴露面用 `bind_host` 配置收敛。
- **`webhook_path` 是路由的唯一来源**（默认 `/webhook`）：归一化逻辑在 `src/core/config.py:normalize_webhook_path`，补前导 `/`、去尾 `/`，含空白/查询串/`{}` 等非法字符时回落默认值。AstrBot 的统一 Webhook 面向平台适配器，插件 Web API 需 Dashboard 鉴权，GitHub 无法调用，因此插件仍用独立 aiohttp 服务；与 AstrBot 共用域名时用 `bind_host: 127.0.0.1` + `webhook_path` 走同一反向代理。
- **多目标发送**：`repo_routes`（JSON 文本）由 `src/services/repo_router.py` 解析，匹配顺序为精确仓库 → `owner/*` → `*` → `target_umo`；空数组表示静音。`send_message(message, target_umos)` 逐目标发送且单目标失败不阻塞其它目标，LLM 链路同样把 `target_umos` 透传到降级发送，勿改回只读 `target_umo`。
- **事件订阅**：`enabled_events` 由 `src/services/event_filter.py` 解析，留空表示全部启用；配置非法时返回空集合（禁用全部可订阅事件）而不是当作未配置，防止拼写错误意外放开全部事件。新增事件类型须同时加入 `SUPPORTED_EVENT_TYPES`、schema 提示与文档。`workflow_run` 只转发 `completed`，避免 CI 进行中状态刷屏。
- **LLM 是可选增强**：任何 LLM 异常、空输出、超时，或输出丢失模板中的核心链接，都必须回落到模板消息，不能让 webhook 请求失败。
- **测试约定**：测试用 `from src.xxx import ...`；formatter/常量这类纯逻辑直接单测，新增动作映射时同步补参数化用例。

## 扩展方式

新增 GitHub 事件（口径见 `docs/06-development.md` 与 `docs/07-project-structure.md`）：

1. `src/handlers/<event>_handler.py` 实现 `async def handle_<event>_event(data, context)`；
2. `src/formatters/<event>_formatter.py` 实现纯函数格式化；
3. 在 `src/core/plugin.py` 的 `handle_webhook()` 中按 `X-GitHub-Event` 增加路由；
4. 补 `tests/` 单测，并更新 `_conf_schema.json`（若新增配置）与 `docs/03-usage.md`；`CHANGELOG.md` 不随日常 PR 更新。

插件配置项的唯一来源是 `_conf_schema.json`（AstrBot WebUI 据此渲染），代码侧默认值集中在 `src/core/constants.py`。

## 仓库工作流（强制）

- **提交信息**：全部使用中文，遵循 Angular 格式 `<type>(<scope>): <subject>`（如 `fix(formatter): 修正 PR 同步动作常量`）。
- **CHANGELOG.md**：只在发版时统一整理（与 `metadata.yaml` 版本号一起提交），日常 PR 不写入未发布条目。
- **GPG 签名**：每次 `git commit` 前必须先执行 `bash install_gpg_keys.sh`，并在**同一条 shell 命令里** `source .git/gpg-commit-env.sh`，再用 `git commit -S`；推送前确认 `git log --show-signature` 为 `Good signature`。
- **pre-commit**：提交前 `pre-commit run --all-files`，black / isort / flake8 / mypy / pydocstyle / pylint(E) / gitleaks / shellcheck 必须全绿。
- **分支与 PR**：禁止直接推送 `main`，一律特性分支 + PR。`.cnb.yml` 会把 `main` 强制同步到 GitHub 镜像仓库 `xiaomizhoubaobei/astrbot_plugin_gitpulse`，因此**同一变更还必须在 GitHub 镜像仓库创建同名同分支的 PR**（AGENTS.md §3.5）。
- **发版**：`metadata.yaml` 的 `version` 先合入，再在 GitHub 镜像仓库打 `v<version>` tag（`release.yml` 会校验 tag 与版本一致）。

### 提交签名排错（本仓库已踩过的坑）

- **`no_user` / `unknown_key`（同源，最常见）**：本地 `git verify-commit` 正常，但平台显示「无法验证」。平台按 **committer 邮箱**关联用户并据其查找密钥：云原生环境注入的 `GIT_COMMITTER_EMAIL=cnb@cnb.local` 会让 GitHub 判 `no_user`、CNB 判 `unknown_key`——**此时签名密钥其实已登记，`unknown_key` 不等于缺公钥**，不要因此重新生成或上传密钥。
  - 检查：`git log -1 --format='%ae / %ce'`，committer 必须是账号已验证邮箱（`qixiaoxin@stu.sqxy.edu.cn`）。
  - 修复：`install_gpg_keys.sh` 会生成 `.git/gpg-commit-env.sh`；**必须在执行 `git commit` / `git rebase` 的同一条 shell 命令里**先 `source .git/gpg-commit-env.sh`——每次 Bash 工具调用都是新 shell，单独 source 后另起一条命令不生效，平台的 `GIT_COMMITTER_*` 会重新覆盖；`~/.bashrc` 只对交互式新终端生效。
- **`unknown_key`（真正的未登记）**：仅当 committer 邮箱正确时才按密钥未登记排查。密钥须同时存在于 CNB 与 GitHub 账号；可分别用 `GET https://api.cnb.cool/user/gpg-keys`（`Accept: application/json` + `CNB_TOKEN`）和 `GET https://api.github.com/users/<login>/gpg_keys` 核对。注意签名常落在**子密钥**上：先 `git verify-commit --raw <sha>` 取实际指纹，再核对平台返回的 `subkeys`。**严禁自行生成新密钥**（新公钥未登记会被判 `unknown_key`）。
- **重签已有提交 / 分支落后 main**：先 `source .git/gpg-commit-env.sh`，再 `git rebase --force-rebase --gpg-sign origin/main`（普通 `rebase` 在分支已基于 main 时会 no-op，必须加 `--force-rebase`）。rebase 同样受 committer 环境变量影响，**重签后务必复核 `%ce` 与 `git verify-commit`**；随后对 CNB 与 GitHub 镜像两个远端 force-push，全部提交 SHA 会变化，PR 自动更新。
- **验证签名**：本地 `git verify-commit --raw <sha>` 应含 `GOODSIG` / `VALIDSIG`；GitHub `GET /repos/<owner>/<repo>/commits/<sha>` 的 `.commit.verification.verified` 与 CNB `GET /{repo}/-/git/commits/<sha>` 的 `.commit.verification.verified` 均应为 `true`。
- **签名未通过禁止 push**；`.cnb.yml` 同步镜像时不会重签，CNB 与 GitHub 两侧看到的必须是同一份已签名历史。

### Issue 关闭与评论（CNB）

关闭 Issue 前先判断问题是否仍然成立。若代码已修复，不要为了「按 Issue 修改」而制造无意义改动；应在评论中说明现状与已修复证据。

- **关闭必须带说明**：先在 Issue 下发表评论，再修改状态；禁止只关闭不解释（避免协作者看不到关闭原因）。
- **commit 引用强制可跳转**：评论中出现的提交号必须写成 Markdown 链接，禁止写成行内代码；链接使用完整 SHA：
  `[<short-sha>](https://cnb.cool/<repo>/-/commit/<full-sha>)`。
- **评论内容**应包含：问题现状、修复提交（若有，须带可跳转链接）、关键验证结果（如 `flake8` / `pytest`，或无需改动的依据）。
- **链接或表述有误时更新原评论**，使用评论 PATCH 接口修正，不要重复刷评论。

CNB API（`<repo>` 形如 `XMZZUZHI/Astrbot/astrbot_plugin_github_webhook`，请求头需 `Authorization: Bearer $CNB_TOKEN`、`Accept: application/json`、`Content-Type: application/json`）：

```bash
# 1. 关闭前先发表说明（Markdown 链接保证提交号可点击）
curl -sS -X POST \
  -H "Authorization: Bearer $CNB_TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"body":"已在 [30d7510](https://cnb.cool/<repo>/-/commit/<full-sha>) 修复……"}' \
  "https://api.cnb.cool/<repo>/-/issues/<number>/comments"

# 2. 关闭 Issue（state 与 state_reason 必须同时提供）
curl -sS -X PATCH \
  -H "Authorization: Bearer $CNB_TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"state":"closed","state_reason":"completed"}' \
  "https://api.cnb.cool/<repo>/-/issues/<number>"
```

- `state_reason` 取值：`completed`（已完成）、`not_planned`（不计划修复）、`reopened`（重新打开）。
- 更新已发表的评论：`PATCH https://api.cnb.cool/<repo>/-/issues/<number>/comments/<comment_id>`，body 为 `{"body":"..."}`。
- 关闭后复核：`GET https://api.cnb.cool/<repo>/-/issues/<number>` 的 `state` 应为 `closed`、`comment_count` 至少为 1；必要时抓取 Issue 页面确认 Markdown 链接已正确渲染。

## 百炼记忆库（Claude Code 工作纪律）

本组织用阿里云百炼共享记忆库跨仓库沉淀可复用结论。Claude Code 必须主动读写，不等用户提醒；具体命令见 `.claude/skills/bailian-memory/SKILL.md`。

- **开场检索**：非一次性任务开始前先 `search`（用任务关键词或报错原文，`similarity_threshold` 0.5~0.7）；命中优先复用，返回空属正常，不重试。
- **收尾沉淀**：仅当产出跨仓库可复用结论（平台约定、踩坑解法、用户长期偏好、字段口径）时 `add`；一次一条、≤512 字符，写清「结论 + 适用场景」，并带 `repo_slug` / `repo_scope` / `user_identity` 元数据。
- **先查重再写**：已有结论用 `update` 补充，不要重复 `add`；结论失效用 `update` 修正或 `delete` 退役（删前先 `search` / `list` 确认 `memory_node_id`，删除不可恢复）。
- **不写入**：逐次任务流水、一次性 diff、未脱敏的 Token / 密钥 / 隐私、只在本仓库成立的临时信息（应放仓库文档或 `meta_data`）。
- **入口**：NPC 运行时用 `/app/scripts/bailian-memory.py`；开发容器无该脚本时按技能内的 HTTP API 调用。**禁止**把该脚本复制进本仓库（属 SuperNPC 运行时，复制会造成版本分叉）。

## CI 概览

- `.github/workflows/coverage.yml`：push/PR 到 `main` 时在 Python 3.12/3.13/3.14 上跑测试，`--source=src` 统计覆盖率，仅 3.12 上报 Codecov（`codecov.yml`，项目/补丁门禁均为 informational）。
- 其余 GitHub Actions：CodeQL、Bandit/DevSkim/trufflehog 安全扫描、依赖审计、Scorecard，以及 `ai-audit-scheduled.yml`（CodeQL 完成后用 LLM 分流告警，`triage`/`publish` 两段式隔离令牌）。
- CNB 侧 `.cnb.yml` 只负责开发容器与把 `main` 同步到 GitHub 镜像，不做覆盖率上报。

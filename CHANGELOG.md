# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 新增
- 新增 `ACCESSIBILITY.md` 无障碍声明，说明无障碍目标、适用范围、已知限制、贡献要求与问题反馈渠道
  - README、文档索引与 `CHANGELOG.md` 同步补充入口
  - GitHub Community Standards 会自动将根目录声明识别为无障碍页面
- 新增 `bind_host` 配置项，Webhook 监听地址不再硬编码（默认 `0.0.0.0`，可改为 `127.0.0.1` 配合反向代理）
  - 修复 `security-scan` 长期红灯：Bandit `B104`（Possible binding to all interfaces）命中 `src/core/plugin.py`
  - 默认值集中到 `src/core/constants.py:DEFAULT_BIND_HOST`，配置项缺失时按默认值兜底
- CI 同步到 GitHub 镜像仓库时开启强制推送（`.cnb.yml` 的 `git-sync` 步骤新增 `force: true`）
  - 即使 CNB 与镜像仓库历史分叉，也能以 CNB 侧为准强制覆盖，避免同步失败或半同步状态

### 更改
- Release 工作流（`.github/workflows/release.yml`）触发条件由 `push: main`（`paths: '**/*.py'`）改为 **`v*` tag push**
  - 发布节奏改由「什么时候打 tag」决定，代码合入与发版解耦；main 上的中间态不再意外变成 Release
  - 新增 tag 与 `metadata.yaml` 版本一致性校验：tag 名必须等于 `v<metadata version>`，否则第 3 步直接失败
  - 证据包验签身份由 `@refs/heads/main` 改为 `@${{ github.ref }}`（tag 触发即 `refs/tags/<tag>`）
  - `workflow_dispatch` 手动触发保留，且不受 tag/版本一致性校验约束
  - 说明：CNB 侧 `.cnb.yml` 只同步 `main` 分支、不搬 tag，故该工作流实际由 GitHub 镜像仓库侧的 tag push 触发

### 安全
- 同步 GitHub 镜像仓库 StepSecurity 安全加固 PR（xiaomizhoubaobei/astrbot_plugin_gitpulse#14）
  - `.pre-commit-config.yaml` 新增 `gitleaks`（密钥扫描）、`shellcheck`（Shell 脚本检查）、`pylint` 三个钩子
  - `.github/dependabot.yml` 新增 `directory: /src` 的 pip 生态条目，跟踪 `src/requirements.txt`
  - 本地适配：`shellcheck` 改用 `shellcheck-py/shellcheck-py`（自带二进制，避免 pre-commit.ci 无 `shellcheck` 命令而失败）；
    `pylint` 仅启用 `E` 级并关闭 `E0401`/`E0402`，不引入与 black/flake8/pydocstyle 重叠的风格噪声
- 修复新增钩子暴露的两处 Shell 问题（此前无人检查，一直潜伏）
  - `scripts/test-webhook.sh`：改为数组拼装 curl 参数，去掉 `eval` 与未加引号的变量展开（消除命令注入面与 SC2086）
  - `install_gpg_keys.sh`：`retry_run` 的首次退避改用已声明的 `CURL_RETRY_SLEEP`，不再声明后闲置（SC2034）

### 修复
- AI 审计示例（`.github/workflows/ai-audit-scheduled.yml`）：公告接口失败不再拖垮整批告警处理
  - `GH_PAT` 缺 `Repository security advisories` 权限时，列公告/建公告的失败不再被
    `set -euo pipefail` 放大成 job 级退出，改为**跳过该条、继续处理后续告警**，并在结尾汇总计入「跳过 N 条」
  - 列公告失败改「先赋值、由退出码分流」，避免 `if` 条件上下文吞掉失败后把诊断文本当作公告 ID 写进公开 Issue
  - 建公告失败改判退出码（403/404 时 `curl -f` 非 0 但响应体仍是 JSON，只判空串会被 `pipefail` 终止）
  - 两个去重辅助函数新增翻页硬上限 `MAX_DEDUPE_PAGES`，防上游异常时无限翻页拖到 Actions 超时

## [0.0.1] - 2026-10-12

### 更改
- 插件更名为 **GitPulse**：插件 ID 由 `astrbot_plugin_github_webhook` 调整为 `astrbot_plugin_gitpulse`，展示名调整为「GitPulse 插件」
- 版本号重置为 `0.0.1`，仓库地址更新为 `https://github.com/xiaomizhoubaobei/astrbot_plugin_gitpulse`
- 同步更新 README、docs、templates 与脚本中的插件名、配置文件名与示例输出

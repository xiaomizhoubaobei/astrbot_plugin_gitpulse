---
name: preflight
description: GitPulse 提交前门禁。准备 commit / push / 开 PR、分支落后 main 需重签，或测试因仓库根的上游 astrbot/ 副本报导入错误时使用。
---

# 提交前门禁（preflight）

按顺序执行，任一步失败先修复再继续。规则出处见 `CLAUDE.md` 的「常用命令」「仓库工作流（强制）」与「提交签名排错」。

## 1. 同步 main（落后时重签 rebase）

```bash
git fetch origin --prune
git rev-list --left-right --count origin/main...HEAD   # 左=落后提交数，右=领先提交数
```

左侧不为 0 时，在**同一条 shell** 里重签 rebase（普通 rebase 在已基于 main 时会 no-op，必须加 `--force-rebase`）：

```bash
source .git/gpg-commit-env.sh && git rebase --force-rebase --gpg-sign origin/main
```

rebase 后逐个核对本次分支的提交：

```bash
git log --format='%h %cn / %ce' origin/main..HEAD
for c in $(git rev-list origin/main..HEAD); do
  git verify-commit --raw "$c" | grep -E 'GOODSIG|VALIDSIG'
done
```

committer 必须是账号已验证邮箱；重签后 SHA 全变，推送时两端都要 `--force-with-lease`。

## 2. 移开上游副本并跑测试 / 检查（同一 shell）

CNB 开发容器会把上游 AstrBot 的 `astrbot/` 包复制到仓库根，导致 pytest 绕过 `tests/conftest.py` 的桩、mypy 扫到数百个无关错误、isort 改变 import 分组。**测试与 lint 必须在副本移开后执行，结束再复原**：

```bash
set -e
PYTHON="${PYTHON:-/opt/conda/bin/python3}"   # 选已装依赖的解释器
ASIDE=""
for d in astrbot AstrBot; do
  if [ -d "$d" ]; then
    ASIDE="$d"
    mv "$d" "/tmp/${d}.preflight-aside"
  fi
done
restore() { [ -n "$ASIDE" ] && mv "/tmp/${ASIDE}.preflight-aside" "./${ASIDE}"; }
trap restore EXIT

"$PYTHON" -m pytest tests -q
"$PYTHON" -m coverage run --source=src -m pytest tests -q
"$PYTHON" -m coverage report -m
"$PYTHON" -m pre_commit run --all-files
```

- 依赖缺失时先 `"$PYTHON" -m pip install -r requirements-dev.txt pre-commit`。
- `astrbot/`、`AstrBot/` 均在 `.gitignore` 中，**不要提交**；trap 复原失败时手工移回。
- isort 首次可能改写 import 分组，重跑一遍确认全部 Passed。

## 3. GPG 签名提交（同一 shell）

```bash
bash install_gpg_keys.sh
source .git/gpg-commit-env.sh && git add -A && git commit -S -F - <<'EOF'
<type>(<scope>): <中文 subject>

<正文>

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
```

`source` 必须与 `git commit` 在同一条命令里；另起命令会被平台注入的 `GIT_COMMITTER_EMAIL` 覆盖，导致 `no_user` / `unknown_key`。提交后核对：

```bash
git log -1 --format='%cn / %ce'          # committer 必须是账号已验证邮箱
git verify-commit --raw HEAD | grep -E 'GOODSIG|VALIDSIG'
```

签名未通过禁止 push；改历史后用 `git rebase --force-rebase --gpg-sign` 重签，详见 `CLAUDE.md`。

## 4. 双端推送与 PR

```bash
git push origin <branch>
git -c credential.helper='!gh auth git-credential' \
  push https://github.com/xiaomizhoubaobei/astrbot_plugin_gitpulse.git <branch>:<branch>
```

- 改过历史时两端都用 `--force-with-lease`；不要给一次性 GitHub URL 加 `-u`，否则会把该 URL 设为上游，后续 `git push` 目标容易混淆。
- 建 PR（两端同名同分支）：
  - GitHub：`gh pr create --repo xiaomizhoubaobei/astrbot_plugin_gitpulse --base main --head <branch> --title ... --body ...`；改标题/描述用 `gh pr edit <number>`。
  - CNB：`POST https://api.cnb.cool/XMZZUZHI/Astrbot/astrbot_plugin_github_webhook/-/pulls`，body 传 `title` / `body` / `head`（源分支名）/ `base`；更新已存在 PR 用 `PATCH /-/pulls/<number>`。
- 推送后核对 GitHub commit 的 `.commit.verification.verified == true`，并确认两端 PR 指向同一 SHA。

## 完成标准

- 分支不落后 `origin/main`（`git rev-list --left-right --count origin/main...HEAD` 左侧为 0）
- pytest 全绿、coverage 不下降
- pre-commit（black / flake8 / isort / mypy / pydocstyle / pylint / gitleaks / shellcheck）全绿
- 提交为 `Good signature`，committer 邮箱已验证
- CNB 与 GitHub 分支与 PR 指向同一提交

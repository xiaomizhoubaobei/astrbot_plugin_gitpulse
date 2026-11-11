#!/usr/bin/env bash
#
# PR Lens 自托管发布（在 CNB pull_request job 中运行）。
#
# 流程：校验 PR 头提交 → 分析 diff → 渲染 SVG → 向 selfhost/fc 的 mint 服务
# 换取短期 COS 预签名 URL → 上传图片 → 生成评论 → 创建/更新同一条 PR 评论。
#
# 相对于官方 plugin.sh：不改写仓库分支，不需要 repo-code:rw；图片走腾讯云 COS，
# 评论只引用公开 URL。CNB 令牌只用于调用 CNB API 和向 mint 服务证明 PR 身份。
#
# 评论查找/更新与 CLI 调用部分改编自 PR Lens 的 plugin.sh（MIT License）。

set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

note() {
  echo "$*" >&2
}

HOST="${PR_LENS_HOST:-}"
[ -n "${HOST}" ] || fail "PR_LENS_HOST is empty; deploy selfhost/fc and set it in .cnb.yml"
HOST="${HOST%/}"

TOKEN="${CNB_TOKEN:-}"
[ -n "${TOKEN}" ] || fail "CNB_TOKEN is empty; this script only runs inside a CNB pipeline"

REPO_SLUG="${CNB_REPO_SLUG:-}"
[ -n "${REPO_SLUG}" ] || fail "CNB_REPO_SLUG is empty"

API_ENDPOINT="${CNB_API_ENDPOINT:-https://api.cnb.cool}"
API_ENDPOINT="${API_ENDPOINT%/}"
WEB_ENDPOINT="${CNB_WEB_ENDPOINT:-https://${CNB_WEB_HOST:-cnb.cool}}"
WEB_ENDPOINT="${WEB_ENDPOINT%/}"
REPO_URL="${CNB_REPO_URL_HTTPS:-${WEB_ENDPOINT}/${REPO_SLUG}}"

PR_NUMBER="${CNB_PULL_REQUEST_IID:-}"
BASE_SHA="${CNB_PULL_REQUEST_TARGET_SHA:-}"
HEAD_SHA="${CNB_PULL_REQUEST_SHA:-${CNB_COMMIT:-}}"
[ -n "${PR_NUMBER}" ] && [ -n "${BASE_SHA}" ] && [ -n "${HEAD_SHA}" ] \
  || fail "PR Lens needs pull request context (CNB_PULL_REQUEST_IID / TARGET_SHA / SHA)"

PROVIDER="${PR_LENS_PROVIDER:-cnb}"
COMMENT="${PR_LENS_COMMENT:-true}"
BRANDING="${PR_LENS_BRANDING:-true}"
WORK="${PR_LENS_WORK:-${CNB_BUILD_WORKSPACE:-$PWD}/.pr-lens}"
mkdir -p "${WORK}"

command -v pr-lens > /dev/null 2>&1 \
  || fail "pr-lens CLI not found; run this in the PR Lens plugin image (has /usr/local/bin/pr-lens)"
cli() {
  pr-lens "$@"
}

# 内置 CNB 模型只需要流水线自带的 CNB_TOKEN，不需要额外密钥仓库。
export PR_LENS_API_KEY="${TOKEN}"

# ---------------------------------------------------------------------------
# Git：确认 base / head 提交都在当前检出里；浅克隆时自动补全历史。
# ---------------------------------------------------------------------------

git config --global --add safe.directory "$(pwd)" 2> /dev/null || true

ensure_commit() {
  local sha="$1"
  local label="$2"

  git cat-file -e "${sha}^{commit}" 2> /dev/null && return 0

  note "the ${label} commit ${sha} is not in this checkout; deepening the clone"
  git fetch --quiet --unshallow origin 2> /dev/null \
    || git fetch --quiet --depth=1000 origin 2> /dev/null \
    || true

  git cat-file -e "${sha}^{commit}" 2> /dev/null \
    || fail "the ${label} commit ${sha} is not in this checkout even after deepening it; do not configure a shallow clone for this job"
}

ensure_commit "${BASE_SHA}" base
ensure_commit "${HEAD_SHA}" head

# ---------------------------------------------------------------------------
# 在模型调用前确认这次运行仍然对应当前 PR 头提交，避免浪费额度。
# ---------------------------------------------------------------------------

api() {
  curl -fsS \
    --header "Authorization: Bearer ${TOKEN}" \
    --header "Accept: application/vnd.cnb.api+json" \
    "$@"
}

json() {
  node -e "let d='';process.stdin.on('data',(c)=>d+=c).on('end',()=>{${1}})"
}

overtaken() {
  local current

  if ! current="$(
    api "${API_ENDPOINT}/${REPO_SLUG}/-/pulls/${PR_NUMBER}" \
      | json "const pr=JSON.parse(d);const sha=pr.head&&pr.head.sha;if(!sha)process.exit(1);console.log(sha)"
  )"; then
    fail "could not ask CNB for the head commit of pull request #${PR_NUMBER}"
  fi

  if [ "${current}" = "${HEAD_SHA}" ]; then
    return 1
  fi

  note "pull request #${PR_NUMBER} now points at ${current}; leaving the comment to the run for that commit"
  return 0
}

if overtaken; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 分析并渲染。CNB 评论页只有一种主题，用 neutral 保持单一渲染。
# ---------------------------------------------------------------------------

cli analyze \
  --base "${BASE_SHA}" \
  --head "${HEAD_SHA}" \
  --pr "${PR_NUMBER}" \
  --forge cnb \
  --repo-url "${REPO_URL}" \
  --provider "${PROVIDER}" \
  --api-key-env PR_LENS_API_KEY \
  --out "${WORK}/graph.json"

cli render "${WORK}/graph.json" --out "${WORK}/assets" --theme neutral

# ---------------------------------------------------------------------------
# 向自托管 mint 服务换取短期上传 URL，再逐个 PUT 到 COS。
# ---------------------------------------------------------------------------

FILES_JSON="$(
  node -e '
const fs = require("node:fs");
const dir = process.argv[1];
const files = fs.readdirSync(dir).filter((file) => file.endsWith(".svg")).sort();
process.stdout.write(JSON.stringify(files));
' "${WORK}/assets"
)"
[ -n "${FILES_JSON}" ] && [ "${FILES_JSON}" != "[]" ] || fail "no SVGs rendered in ${WORK}/assets"

REQUEST_BODY="$(
  node -e '
const [repo, pr, head, files] = process.argv.slice(1);
process.stdout.write(JSON.stringify({ repo, pr: Number(pr), headSha: head, files: JSON.parse(files) }));
' "${REPO_SLUG}" "${PR_NUMBER}" "${HEAD_SHA}" "${FILES_JSON}"
)"

MINT_JSON="$(
  curl -fsS -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "${REQUEST_BODY}" \
    "${HOST}/api/sessions"
)" || fail "could not mint upload session at ${HOST}"

RUN_ID="$(
  node -e 'const d = JSON.parse(process.argv[1]); if (!d.id) process.exit(1); process.stdout.write(d.id);' "${MINT_JSON}"
)" || fail "mint response has no session id"

ASSET_BASE="$(
  node -e 'const d = JSON.parse(process.argv[1]); if (!d.assetBase) process.exit(1); process.stdout.write(d.assetBase);' "${MINT_JSON}"
)" || fail "mint response has no assetBase"

node -e '
const d = JSON.parse(process.argv[1]);
for (const upload of d.uploads) console.log(upload.file + "\t" + upload.url);
' "${MINT_JSON}" > "${WORK}/uploads.tsv"

while IFS=$'\t' read -r file url; do
  [ -n "${file}" ] || continue
  curl -fsS -X PUT \
    -H "Content-Type: image/svg+xml" \
    --data-binary "@${WORK}/assets/${file}" \
    "${url}" > /dev/null \
    || fail "could not upload ${file} to COS"
done < "${WORK}/uploads.tsv"

note "PR Lens assets uploaded: ${ASSET_BASE} (session ${RUN_ID})"

if [ "${COMMENT}" != "true" ]; then
  note "comment disabled; assets are at ${ASSET_BASE}"
  exit 0
fi

# ---------------------------------------------------------------------------
# 生成评论并维护同一条 PR 评论。
# ---------------------------------------------------------------------------

COMMENT_ARGS=(
  --target cnb
  --graph "${WORK}/assets/drawn.graph.json"
  --manifest "${WORK}/assets/manifest.json"
  --asset-base-url "${ASSET_BASE}"
)
[ "${BRANDING}" = "true" ] || COMMENT_ARGS+=(--no-branding)
cli comment "${COMMENT_ARGS[@]}" --out "${WORK}/comment.md"

MARKER="$(cli comment --print-marker --target cnb)"
COMMENT_AUTHOR="${CNB_TOKEN_USER_NAME:-}"

: > "${WORK}/comments.jsonl"
PAGE=1
while :; do
  api "${API_ENDPOINT}/${REPO_SLUG}/-/pulls/${PR_NUMBER}/comments?page=${PAGE}&page_size=100" \
    > "${WORK}/comments.page.json" \
    || fail "could not list the comments on pull request #${PR_NUMBER}"
  json "const v=JSON.parse(d);for(const c of (Array.isArray(v)?v:[]))console.log(JSON.stringify(c))" \
    < "${WORK}/comments.page.json" >> "${WORK}/comments.jsonl"
  LINES="$(wc -l < "${WORK}/comments.jsonl")"
  if [ "${LINES}" -lt $((PAGE * 100)) ]; then
    break
  fi
  PAGE=$((PAGE + 1))
done

MINE="$(
  node - "${WORK}/comments.jsonl" "${COMMENT_AUTHOR}" "${MARKER}" <<'FIND'
const fs = require("node:fs");
const [commentsPath, author, marker] = process.argv.slice(2);
for (const line of fs.readFileSync(commentsPath, "utf8").split("\n")) {
  if (line === "") continue;
  const comment = JSON.parse(line);
  if (typeof comment.body !== "string" || !comment.body.startsWith(marker)) continue;
  if (author !== "" && comment.author?.username !== author) continue;
  if (comment.id === undefined || comment.id === null) continue;
  process.stdout.write(String(comment.id));
  break;
}
FIND
)"

# 发布和列举评论都会花时间，写入前再校验一次头提交。
if overtaken; then
  exit 0
fi

BODY_JSON="${WORK}/comment.json"
node -e 'const fs = require("node:fs"); process.stdout.write(JSON.stringify({ body: fs.readFileSync(process.argv[1], "utf8") }))' \
  "${WORK}/comment.md" > "${BODY_JSON}"

COMMENT_URL="${WEB_ENDPOINT}/${REPO_SLUG}/-/pulls/${PR_NUMBER}"

if [ -n "${MINE}" ]; then
  if api -X PATCH \
    --header "Content-Type: application/json" \
    --data-binary "@${BODY_JSON}" \
    "${API_ENDPOINT}/${REPO_SLUG}/-/pulls/${PR_NUMBER}/comments/${MINE}" > /dev/null; then
    echo "✓ the PR Lens comment on ${COMMENT_URL} is up to date"
    exit 0
  fi

  note "could not update comment ${MINE}; posting a new one"
fi

CREATED="$(
  api -X POST \
    --header "Content-Type: application/json" \
    --data-binary "@${BODY_JSON}" \
    "${API_ENDPOINT}/${REPO_SLUG}/-/pulls/${PR_NUMBER}/comments" \
    | json "const c=JSON.parse(d);if(c.id!==undefined&&c.id!==null)process.stdout.write(String(c.id))"
)" || fail "could not post the PR Lens comment on pull request #${PR_NUMBER}"

echo "✓ the PR Lens comment is on ${COMMENT_URL} (comment ${CREATED})"

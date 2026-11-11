#!/usr/bin/env bash
#
# 把 selfhost/fc 部署为阿里云函数计算（FC 3.0）HTTP 函数。
#
# 依赖：aliyun CLI（已配置 ALIBABA_CLOUD_ACCESS_KEY_ID / SECRET）、python3、jq。
# 腾讯云 COS 的密钥通过 FC 环境变量注入，不会进入代码包或仓库。

set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

REGION="${FC_REGION:-cn-shanghai}"
FUNCTION="${FC_FUNCTION:-pr-lens}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../fc" && pwd)"

command -v aliyun > /dev/null 2>&1 || fail "aliyun CLI is required"
command -v jq > /dev/null 2>&1 || fail "jq is required"
command -v python3 > /dev/null 2>&1 || fail "python3 is required"

[ -n "${TENCENT_SECRET_ID:-}" ] || fail "TENCENT_SECRET_ID is empty"
[ -n "${TENCENT_SECRET_KEY:-}" ] || fail "TENCENT_SECRET_KEY is empty"

ENV_JSON="$(
  node -e '
const names = [
  "TENCENT_SECRET_ID",
  "TENCENT_SECRET_KEY",
  "COS_BUCKET",
  "COS_REGION",
  "COS_PUBLIC_BASE",
  "CNB_API_ENDPOINT",
  "ALLOWED_REPOS",
  "MINT_PER_HOUR",
  "UPLOAD_EXPIRES_SECONDS",
  "MAX_FILES",
];
const result = {};
for (const name of names) {
  if (process.env[name]) result[name] = process.env[name];
}
process.stdout.write(JSON.stringify(result));
'
)"

ZIP="$(mktemp "${TMPDIR:-/tmp}/pr-lens-fc.XXXXXX.zip")"
trap 'rm -f "${ZIP}"' EXIT

(cd "${ROOT}" && python3 -m zipfile -c "${ZIP}" index.js cos-sign.js package.json)
B64="$(base64 -w0 "${ZIP}")"

CODE="{\"zipFile\":\"${B64}\"}"

if aliyun fc get-function --function-name "${FUNCTION}" --region "${REGION}" > /dev/null 2>&1; then
  echo "==> 更新函数 ${FUNCTION} (${REGION})"
  aliyun fc update-function \
    --function-name "${FUNCTION}" \
    --region "${REGION}" \
    --code "${CODE}" \
    --environment-variables "${ENV_JSON}" \
    --cpu 0.25 \
    --memory-size 256 \
    --timeout 30 > /dev/null
else
  echo "==> 创建函数 ${FUNCTION} (${REGION})"
  aliyun fc create-function \
    --function-name "${FUNCTION}" \
    --region "${REGION}" \
    --runtime nodejs20 \
    --handler index.handler \
    --cpu 0.25 \
    --memory-size 256 \
    --timeout 30 \
    --disk-size 512 \
    --internet-access true \
    --code "${CODE}" \
    --environment-variables "${ENV_JSON}" > /dev/null
fi

if ! aliyun fc list-triggers --function-name "${FUNCTION}" --region "${REGION}" 2> /dev/null \
  | jq -e '.triggers[] | select(.triggerType == "http")' > /dev/null; then
  echo "==> 创建 HTTP 触发器"
  aliyun fc create-trigger \
    --function-name "${FUNCTION}" \
    --region "${REGION}" \
    --trigger-name httpTrigger \
    --trigger-type http \
    --trigger-config '{"methods":["GET","POST","OPTIONS"],"authType":"anonymous","disableURLInternet":false}' \
    --qualifier LATEST > /dev/null
fi

URL="$(
  aliyun fc list-triggers --function-name "${FUNCTION}" --region "${REGION}" \
    | jq -r '.triggers[] | select(.triggerType == "http") | .httpTrigger.urlInternet' \
    | head -n 1
)"
[ -n "${URL}" ] || fail "HTTP trigger URL not found"

echo "==> PR_LENS_HOST=${URL}"

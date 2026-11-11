'use strict';

/**
 * PR Lens 自托管上传凭据服务（阿里云函数计算 FC）。
 *
 * 职责很小：CNB 流水线带着 PR 上下文和内置 CNB_TOKEN 来换一组**短期**的
 * COS 预签名 PUT URL。函数只接受允许仓库、白名单 .svg 文件名，并按来源 IP
 * 限流；预签名 URL 只能写本次运行专属的 COS 前缀，过期即失效。因此：
 *   - 流水线里不需要存放腾讯云长期密钥；
 *   - 上传能力是短期的、范围受限的。
 *
 * 说明：CNB 对公网的 api.cnb.cool / git 端点启用了网络策略拦截（实测返回
 * 412/403），因此默认不在函数侧校验 PR 头提交；如部署在 CNB 放行的网络内，
 * 可设置 VERIFY_CNB=1 开启该校验。
 *
 * 图片本身由腾讯云 COS 直接对外提供（经由 EdgeOne 自定义域名），
 * 评论里只引用这些公开 URL。
 */

const crypto = require('node:crypto');
const { presignedPutUrl } = require('./cos-sign');

const CONFIG = {
  bucket: process.env.COS_BUCKET || 'pr-lens-1329111128',
  region: process.env.COS_REGION || 'ap-shanghai',
  publicBase: (process.env.COS_PUBLIC_BASE || '').replace(/\/+$/, ''),
  cnbApi: (process.env.CNB_API_ENDPOINT || 'https://api.cnb.cool').replace(/\/+$/, ''),
  allowedRepos: (process.env.ALLOWED_REPOS || 'XMZZUZHI/Astrbot/astrbot_plugin_github_webhook')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean),
  mintPerHour: Number(process.env.MINT_PER_HOUR || 30),
  uploadExpiresSeconds: Number(process.env.UPLOAD_EXPIRES_SECONDS || 900),
  maxFiles: Number(process.env.MAX_FILES || 12),
  // CNB 对公网 api.cnb.cool / git 端点启用了网络策略拦截，境外或云上函数调用
  // 会返回 412/403。默认关闭远端校验，改用「限流 + 短期预签名」控制滥用；
  // 若部署在 CNB 允许的网络内，可设 VERIFY_CNB=1 开启 PR 头提交校验。
  verifyCnb: process.env.VERIFY_CNB === '1',
};

const COS_HOST = `https://${CONFIG.bucket}.cos.${CONFIG.region}.myqcloud.com`;
const ASSET_BASE = CONFIG.publicBase || COS_HOST;

// 文件名白名单：只接受单层 .svg 文件名，避免路径穿越和任意对象键。
const FILE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.svg$/;
const SHA_PATTERN = /^[0-9a-f]{40}$/i;

// 每个实例内的简单限流；FC 多实例时各自计数，足以挡住误用和刷量。
const mintWindows = new Map();
const RATE_WINDOW_MS = 60 * 60 * 1000;

function jsonResponse(statusCode, payload, extraHeaders) {
  return {
    statusCode,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'access-control-allow-origin': '*',
      'access-control-allow-methods': 'GET, POST, OPTIONS',
      'access-control-allow-headers': 'authorization, content-type',
      ...(extraHeaders || {}),
    },
    body: JSON.stringify(payload),
    isBase64Encoded: false,
  };
}

function errorResponse(statusCode, code, message) {
  return jsonResponse(statusCode, { error: { code, message } });
}

function parseEvent(event) {
  // FC 3.0 的 Node.js runtime 把 HTTP 事件以 Buffer 传入，需先解析为对象。
  let source = event;
  if (Buffer.isBuffer(event)) {
    try {
      source = JSON.parse(event.toString('utf8'));
    } catch {
      source = {};
    }
  } else if (typeof event === 'string') {
    try {
      source = JSON.parse(event);
    } catch {
      source = {};
    }
  }

  const http = (source.requestContext && source.requestContext.http) || {};
  const method = String(http.method || source.httpMethod || source.method || 'GET').toUpperCase();
  const rawPath = http.path || source.rawPath || source.path || '/';
  const path = String(rawPath).split('?')[0] || '/';

  const headers = {};
  for (const [key, value] of Object.entries(source.headers || {})) {
    headers[String(key).toLowerCase()] = value;
  }

  let body = source.body || '';
  if (source.isBase64Encoded && body) {
    body = Buffer.from(body, 'base64').toString('utf8');
  }

  return {
    method,
    path,
    headers,
    body,
    sourceIp: String(http.sourceIp || source.sourceIp || 'unknown'),
  };
}

function bearerToken(headers) {
  const match = /^Bearer\s+(.+)$/i.exec(String(headers.authorization || ''));
  return match ? match[1].trim() : '';
}

function withinRateLimit(sourceIp) {
  const now = Date.now();
  const recent = (mintWindows.get(sourceIp) || []).filter((at) => now - at < RATE_WINDOW_MS);
  if (recent.length >= CONFIG.mintPerHour) {
    mintWindows.set(sourceIp, recent);
    return false;
  }
  recent.push(now);
  mintWindows.set(sourceIp, recent);
  return true;
}

/**
 * 可选校验：确认 CNB_TOKEN 确实指向该 PR 的当前头提交。
 * 默认关闭，原因见 CONFIG.verifyCnb 的注释；开启后只允许能访问该 PR 的调用方。
 */
async function verifyPullRequest(token, repo, pr, headSha) {
  if (!CONFIG.allowedRepos.includes(repo)) return false;
  if (!CONFIG.verifyCnb) return true;

  const url = `${CONFIG.cnbApi}/${repo}/-/pulls/${pr}`;
  const response = await fetch(url, {
    headers: {
      authorization: `Bearer ${token}`,
      accept: 'application/vnd.cnb.api+json',
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) return false;

  const data = await response.json().catch(() => null);
  return Boolean(data && data.head && data.head.sha === headSha);
}

function normalizeFiles(files) {
  const unique = [];
  for (const name of files) {
    if (typeof name !== 'string' || !FILE_PATTERN.test(name)) return null;
    if (!unique.includes(name)) unique.push(name);
  }
  if (unique.length === 0 || unique.length > CONFIG.maxFiles) return null;
  return unique;
}

async function mintSession(request) {
  const token = bearerToken(request.headers);
  if (!token) return errorResponse(401, 'UNAUTHENTICATED', 'missing CNB token');
  if (!withinRateLimit(request.sourceIp)) {
    return errorResponse(429, 'RATE_LIMITED', 'too many mint requests from this address');
  }

  let payload;
  try {
    payload = JSON.parse(request.body);
  } catch {
    return errorResponse(400, 'INVALID_REQUEST', 'body must be JSON');
  }

  const { repo, pr, headSha, files } = payload || {};
  if (
    typeof repo !== 'string' ||
    !Number.isInteger(pr) ||
    pr <= 0 ||
    typeof headSha !== 'string' ||
    !SHA_PATTERN.test(headSha) ||
    !Array.isArray(files)
  ) {
    return errorResponse(400, 'INVALID_REQUEST', 'repo, pr, headSha and files are required');
  }

  const names = normalizeFiles(files);
  if (!names) {
    return errorResponse(400, 'INVALID_REQUEST', 'files must be 1..N .svg basenames');
  }

  let verified;
  try {
    verified = await verifyPullRequest(token, repo, pr, headSha);
  } catch {
    return errorResponse(502, 'VERIFY_FAILED', 'could not verify the pull request with CNB');
  }
  if (!verified) {
    return errorResponse(403, 'FORBIDDEN', 'token does not match this pull request head');
  }

  const runId = crypto.randomBytes(12).toString('hex');
  const prefix = `pr-lens/pr/${pr}/${headSha.slice(0, 12)}/${runId}`;
  const assetBase = `${ASSET_BASE}/${prefix}`;

  const uploads = names.map((file) => ({
    file,
    method: 'PUT',
    url: presignedPutUrl({
      secretId: process.env.TENCENT_SECRET_ID,
      secretKey: process.env.TENCENT_SECRET_KEY,
      bucket: CONFIG.bucket,
      region: CONFIG.region,
      key: `${prefix}/${file}`,
      expiresSeconds: CONFIG.uploadExpiresSeconds,
    }),
    headers: { 'content-type': 'image/svg+xml' },
  }));

  console.log(`pr-lens mint: repo=${repo} pr=${pr} files=${names.length} ip=${request.sourceIp}`);
  return jsonResponse(200, {
    id: runId,
    assetBase,
    uploads,
    expiresAt: new Date((Math.floor(Date.now() / 1000) + CONFIG.uploadExpiresSeconds) * 1000).toISOString(),
  });
}

exports.handler = async (event) => {
  try {
    const request = parseEvent(event);

    if (request.method === 'OPTIONS') {
      return jsonResponse(204, {});
    }
    if (request.method === 'GET' && request.path === '/healthz') {
      return jsonResponse(200, { ok: true, service: 'pr-lens-selfhost' });
    }
    if (request.method === 'POST' && request.path === '/api/sessions') {
      return await mintSession(request);
    }
    return errorResponse(404, 'NOT_FOUND', 'route not found');
  } catch (error) {
    console.error('pr-lens handler error:', error && error.name ? error.name : 'unknown');
    return errorResponse(500, 'INTERNAL_ERROR', 'internal error');
  }
};

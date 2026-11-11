'use strict';

/** 单元测试：不需要真实 CNB/COS，签名在本地完成。 */

process.env.TENCENT_SECRET_ID = 'test-id';
process.env.TENCENT_SECRET_KEY = 'test-key';
process.env.COS_BUCKET = 'test-bucket';
process.env.COS_REGION = 'ap-shanghai';
process.env.COS_PUBLIC_BASE = 'https://cdn.example';
process.env.ALLOWED_REPOS = 'XMZZUZHI/Astrbot/astrbot_plugin_github_webhook';
process.env.MINT_PER_HOUR = '3';

const test = require('node:test');
const assert = require('node:assert/strict');
const { handler } = require('../index');

const REPO = 'XMZZUZHI/Astrbot/astrbot_plugin_github_webhook';
const HEAD = 'a'.repeat(40);

function makeEvent(method, path, { headers = {}, body = '', ip = '10.0.0.1' } = {}) {
  return {
    version: 'v1',
    rawPath: path,
    headers,
    requestContext: { http: { method, path, sourceIp: ip } },
    body,
    isBase64Encoded: false,
  };
}

function mintEvent({ token = 'test-token', files = ['a.svg'], ip = '10.0.0.2', repo = REPO } = {}) {
  return makeEvent('POST', '/api/sessions', {
    headers: { authorization: `Bearer ${token}` },
    body: JSON.stringify({ repo, pr: 89, headSha: HEAD, files }),
    ip,
  });
}

test('healthz reports the service is up', async () => {
  const response = await handler(makeEvent('GET', '/healthz'));
  assert.equal(response.statusCode, 200);
  assert.equal(JSON.parse(response.body).ok, true);
});

test('mint rejects requests without a token', async () => {
  const response = await handler(
    makeEvent('POST', '/api/sessions', { body: '{}', ip: '10.0.0.3' }),
  );
  assert.equal(response.statusCode, 401);
  assert.equal(JSON.parse(response.body).error.code, 'UNAUTHENTICATED');
});

test('mint rejects non-svg filenames', async () => {
  const response = await handler(mintEvent({ files: ['../../escape.svg'], ip: '10.0.0.4' }));
  assert.equal(response.statusCode, 400);
});

test('mint rejects repositories outside the allowlist', async () => {
  const response = await handler(
    mintEvent({ repo: 'someone/else', ip: '10.0.0.5' }),
  );
  assert.equal(response.statusCode, 403);
});

test('mint returns signed uploads for a valid request', async () => {
  const response = await handler(
    mintEvent({ files: ['architecture.svg', 'data-flow.svg'], ip: '10.0.0.6' }),
  );
  assert.equal(response.statusCode, 200);

  const payload = JSON.parse(response.body);
  assert.match(payload.id, /^[0-9a-f]{24}$/);
  assert.equal(payload.uploads.length, 2);
  assert.match(payload.assetBase, /^https:\/\/cdn\.example\/pr-lens\/pr\/89\/aaaaaaaaaaaa\//);
  for (const upload of payload.uploads) {
    assert.equal(upload.method, 'PUT');
    assert.match(upload.url, /q-signature=[0-9a-f]{40}/);
    assert.match(upload.url, /q-header-list=host/);
  }
});

test('unknown routes return 404', async () => {
  const response = await handler(makeEvent('GET', '/nope', { ip: '10.0.0.7' }));
  assert.equal(response.statusCode, 404);
});

test('mint is rate limited per source address', async () => {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await handler(mintEvent({ ip: '10.0.0.8' }));
    assert.equal(response.statusCode, 200);
  }
  const response = await handler(mintEvent({ ip: '10.0.0.8' }));
  assert.equal(response.statusCode, 429);
});

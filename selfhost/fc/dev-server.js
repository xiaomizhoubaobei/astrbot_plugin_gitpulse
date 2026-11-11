'use strict';

/** 本地调试用的 HTTP 包装器：把请求转换成 FC HTTP 触发器事件。 */

const http = require('node:http');
const { handler } = require('./index');

const port = Number(process.env.PORT || 8080);
const host = process.env.HOST || '127.0.0.1';

const server = http.createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks).toString('utf8');

  const event = {
    version: 'v1',
    rawPath: request.url,
    headers: request.headers,
    requestContext: {
      http: {
        method: request.method,
        path: request.url,
        sourceIp: request.socket.remoteAddress || '127.0.0.1',
      },
    },
    body,
    isBase64Encoded: false,
  };

  try {
    const result = await handler(event, {});
    response.statusCode = result.statusCode;
    for (const [key, value] of Object.entries(result.headers || {})) {
      response.setHeader(key, value);
    }
    response.end(result.body);
  } catch (error) {
    response.statusCode = 500;
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ error: { code: 'INTERNAL_ERROR' } }));
  }
});

server.listen(port, host, () => {
  console.log(`pr-lens selfhost dev server listening on http://${host}:${port}`);
});

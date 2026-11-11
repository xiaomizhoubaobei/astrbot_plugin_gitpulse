'use strict';

/**
 * Tencent Cloud COS 预签名 PUT URL（签名 V5）。
 *
 * 只实现本服务需要的最小子集：无查询参数、仅签 host 头。算法与
 * cos-nodejs-sdk-v5 的 util.js 保持一致，避免在 FC 代码包里携带 SDK。
 */

const crypto = require('node:crypto');

/** COS 的 URL 编码：在 encodeURIComponent 基础上补齐 SDK 替换的保留字符。 */
function safeEncode(value) {
  return encodeURIComponent(value)
    .replace(/!/g, '%21')
    .replace(/'/g, '%27')
    .replace(/\(/g, '%28')
    .replace(/\)/g, '%29')
    .replace(/\*/g, '%2A');
}

/**
 * 生成用于上传单个对象的预签名 URL。
 *
 * @param {object} options
 * @param {string} options.secretId COS SecretId
 * @param {string} options.secretKey COS SecretKey
 * @param {string} options.bucket 存储桶名称（含 APPID 后缀）
 * @param {string} options.region 地域，例如 ap-shanghai
 * @param {string} options.key 对象键
 * @param {number} [options.expiresSeconds] 有效期，默认 900 秒
 * @returns {string} 可直接 PUT 的 HTTPS URL
 */
function presignedPutUrl({ secretId, secretKey, bucket, region, key, expiresSeconds = 900 }) {
  if (!secretId || !secretKey) throw new Error('COS credentials are required');

  // 与 SDK 一致：起始时间回退 1 秒，避免客户端与 COS 的时钟偏差。
  const now = Math.floor(Date.now() / 1000) - 1;
  const keyTime = `${now};${now + expiresSeconds}`;
  const host = `${bucket}.cos.${region}.myqcloud.com`;
  const pathname = `/${key.split('/').map(safeEncode).join('/')}`;

  // 步骤一：SignKey = HMAC-SHA1(SecretKey, KeyTime)
  const signKey = crypto.createHmac('sha1', secretKey).update(keyTime).digest('hex');

  // 步骤二：FormatString 里的 method 必须小写；header 部分与 q-header-list 对应（这里只有 host）
  const formatString = ['put', pathname, '', `host=${safeEncode(host)}`, ''].join('\n');

  // 步骤三：StringToSign = sha1 + KeyTime + SHA1(FormatString)
  const hashed = crypto.createHash('sha1').update(formatString).digest('hex');
  const stringToSign = ['sha1', keyTime, hashed, ''].join('\n');

  // 步骤四：Signature = HMAC-SHA1(SignKey, StringToSign)
  const signature = crypto.createHmac('sha1', signKey).update(stringToSign).digest('hex');

  const query = [
    'q-sign-algorithm=sha1',
    `q-ak=${safeEncode(secretId)}`,
    `q-sign-time=${keyTime}`,
    `q-key-time=${keyTime}`,
    'q-header-list=host',
    'q-url-param-list=',
    `q-signature=${signature}`,
  ].join('&');

  return `https://${host}${pathname}?${query}`;
}

module.exports = { presignedPutUrl };

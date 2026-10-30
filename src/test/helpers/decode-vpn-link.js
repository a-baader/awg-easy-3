'use strict';

// Декодер нужен только тестам; production выдаёт профили, но не принимает vpn://.
const zlib = require('node:zlib');

const qtUncompress = (input) => {
  const source = Buffer.isBuffer(input) ? input : Buffer.from(input);
  if (source.length < 5) throw new TypeError('Invalid qCompress payload');
  const expectedLength = source.readUInt32BE(0);
  const result = zlib.inflateSync(source.subarray(4), { maxOutputLength: 1024 * 1024 });
  if (result.length !== expectedLength) throw new Error('qCompress length mismatch');
  return result;
};

const fromBase64Url = (input) => {
  if (typeof input !== 'string' || !/^[A-Za-z0-9_-]+$/.test(input)) {
    throw new TypeError('Invalid base64url payload');
  }
  const padding = '='.repeat((4 - (input.length % 4)) % 4);
  return Buffer.from(input.replace(/-/g, '+').replace(/_/g, '/') + padding, 'base64');
};

const decodeVpnLink = (link) => {
  if (typeof link !== 'string' || !link.startsWith('vpn://')) {
    throw new TypeError('AmneziaVPN link must start with vpn://');
  }
  const json = qtUncompress(fromBase64Url(link.slice('vpn://'.length)));
  return JSON.parse(json.toString('utf8'));
};

module.exports = { decodeVpnLink, qtUncompress };

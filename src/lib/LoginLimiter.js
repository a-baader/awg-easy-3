'use strict';

const { isIP } = require('node:net');
const { performance } = require('node:perf_hooks');

class LoginLimitError extends Error {
  constructor(retryAfter = 1) {
    super('Слишком много попыток входа. Повторите позже.');
    this.statusCode = 429;
    this.retryAfter = Math.max(1, Math.ceil(retryAfter));
  }
}

class LoginLimiter {
  constructor({ clock = () => performance.now(), maxConcurrent = 2,
    peerBurst = 5, peerRefillMs = 12_000, globalBurst = 20, globalRefillMs = 3_000,
    maxAddresses = 1024, idleMs = 300_000 } = {}) {
    for (const value of [maxConcurrent, peerBurst, peerRefillMs, globalBurst, globalRefillMs, maxAddresses, idleMs]) {
      if (!Number.isSafeInteger(value) || value < 1) throw new TypeError('Лимиты входа должны быть положительными целыми числами');
    }
    if (typeof clock !== 'function') throw new TypeError('Часы ограничителя входа должны быть функцией');
    Object.assign(this, { clock, maxConcurrent, peerBurst, peerRefillMs, globalBurst, globalRefillMs, maxAddresses, idleMs });
    this.active = 0;
    this.addresses = new Map();
    this.global = { tokens: globalBurst, updated: clock() };
  }

  refill(bucket, burst, interval, now) {
    bucket.tokens = Math.min(burst, bucket.tokens + Math.max(0, now - bucket.updated) / interval);
    bucket.updated = now;
  }

  async run(remoteAddress, operation) {
    // Источник берётся только из socket; HTTP-заголовки сюда не передаются.
    let address = typeof remoteAddress === 'string' ? remoteAddress : '';
    if (address.startsWith('::ffff:') && isIP(address.slice(7)) === 4) address = address.slice(7);
    if (!isIP(address)) address = 'unknown';
    const now = this.clock();
    for (const [key, bucket] of this.addresses) {
      if (now - bucket.updated >= this.idleMs) this.addresses.delete(key);
    }
    let peer = this.addresses.get(address);
    if (!peer) {
      if (this.addresses.size >= this.maxAddresses) throw new LoginLimitError(this.idleMs / 1000);
      peer = { tokens: this.peerBurst, updated: now };
      this.addresses.set(address, peer);
    }
    this.refill(peer, this.peerBurst, this.peerRefillMs, now);
    this.refill(this.global, this.globalBurst, this.globalRefillMs, now);
    if (peer.tokens < 1 || this.global.tokens < 1) {
      throw new LoginLimitError(Math.max(
        (1 - peer.tokens) * this.peerRefillMs,
        (1 - this.global.tokens) * this.globalRefillMs,
      ) / 1000);
    }
    peer.tokens -= 1;
    this.global.tokens -= 1;
    // Очередь login отсутствует: при занятых слотах сразу отвечаем 429.
    if (this.active >= this.maxConcurrent) throw new LoginLimitError();
    this.active += 1;
    try {
      return await operation();
    } finally {
      this.active -= 1;
    }
  }
}

module.exports = { LoginLimiter, LoginLimitError };

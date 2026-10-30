'use strict';
const crypto = require('node:crypto');

const validPacket = (service, packet) => {
  if (!Buffer.isBuffer(packet) || packet.length > 65507) return false;
  if (service.name === 'mdns4') return packet.length >= 12 && (packet[2] & 0x78) === 0
    && [4, 6, 8, 10].some(offset => packet.readUInt16BE(offset) > 0);
  return packet.length <= 16384 && /^(?:M-SEARCH \* HTTP\/1\.1|NOTIFY \* HTTP\/1\.1|HTTP\/1\.[01] 200[^\r\n]*)\r\n/i
    .test(packet.toString('utf8')) && packet.includes(Buffer.from('\r\n\r\n'));
};

class DiscoveryGuard {
  constructor({ clock = () => Date.now(), peerPps = 40, globalPps = 400,
    peerBytes = 131072, globalBytes = 1048576, maxEntries = 2048, dedupMs = 1000 } = {}) {
    this.clock = clock;
    this.limits = { peerPps, globalPps, peerBytes, globalBytes };
    this.maxEntries = maxEntries;
    this.dedupMs = dedupMs;
    this.peers = new Map();
    this.recent = new Map();
    this.global = this.bucket(globalPps, globalBytes);
    this.stats = { invalid: 0, duplicate: 0, rate: 0, accepted: 0 };
  }

  bucket(packets, bytes) { return { packets, bytes, at: this.clock() }; }

  refill(bucket, packets, bytes, now) {
    const seconds = Math.max(0, now - bucket.at) / 1000;
    bucket.packets = Math.min(packets, bucket.packets + seconds * packets);
    bucket.bytes = Math.min(bytes, bucket.bytes + seconds * bytes);
    bucket.at = now;
  }

  allow(service, packet, source, fanout, rewritten = packet) {
    if (!validPacket(service, packet)) { this.stats.invalid++; return false; }
    const now = this.clock(), limits = this.limits;
    if (!this.peers.has(source)) {
      if (this.peers.size >= this.maxEntries) { this.stats.rate++; return false; }
      this.peers.set(source, this.bucket(limits.peerPps, limits.peerBytes));
    }
    const peer = this.peers.get(source);
    this.refill(peer, limits.peerPps, limits.peerBytes, now);
    this.refill(this.global, limits.globalPps, limits.globalBytes, now);
    // Учитывается усиление fan-out, а не только размер входящего пакета.
    const cost = Math.max(1, fanout), bytes = Math.max(packet.length, rewritten.length) * cost;
    if (peer.packets < cost || peer.bytes < bytes || this.global.packets < cost || this.global.bytes < bytes) {
      this.stats.rate++; return false;
    }
    peer.packets -= cost; peer.bytes -= bytes;
    this.global.packets -= cost; this.global.bytes -= bytes;
    while (this.recent.size && this.recent.values().next().value <= now) this.recent.delete(this.recent.keys().next().value);
    const fingerprint = value => service.name + ':' + crypto.createHash('sha256').update(value).digest('hex');
    const key = fingerprint(packet);
    if ((this.recent.get(key) ?? 0) > now) { this.stats.duplicate++; return false; }
    for (const hash of new Set([key, fingerprint(rewritten)])) {
      if (!this.recent.has(hash) && this.recent.size >= this.maxEntries) this.recent.delete(this.recent.keys().next().value);
      this.recent.set(hash, now + this.dedupMs);
    }
    this.stats.accepted++;
    return true;
  }

  refresh(homes) { for (const source of this.peers.keys()) if (!homes.has(source)) this.peers.delete(source); }
  clear() { this.peers.clear(); this.recent.clear(); }
}

module.exports = { DiscoveryGuard, validPacket };

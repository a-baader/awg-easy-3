'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { DiscoveryGuard } = require('../lib/DiscoveryGuard');
const mdns = { name: 'mdns4' };
const packet = number => { const value = Buffer.alloc(20); value.writeUInt16BE(1, 4); value.writeUInt32BE(number, 12); return value; };

test('discovery ограничивает peer, общий объём, fan-out и повторные петли', () => {
  let now = 0;
  const guard = new DiscoveryGuard({ clock: () => now, peerPps: 4, globalPps: 6 });
  assert.equal(guard.allow(mdns, packet(1), 'Home A', 2), true);
  assert.equal(guard.allow(mdns, packet(1), 'Home B', 1), false);
  assert.equal(guard.stats.duplicate, 1);
  assert.equal(guard.allow(mdns, packet(2), 'Home A', 3), false);
  assert.equal(guard.allow(mdns, packet(3), 'Home B', 3), true);
  assert.equal(guard.allow(mdns, packet(4), 'Home C', 1), false);
  now = 1100;
  assert.equal(guard.allow(mdns, packet(1), 'Home A', 1), true);
  assert.equal(guard.allow(mdns, Buffer.from('мусор'), 'Home A', 1), false);
  guard.refresh(new Set(['Home A'])); assert.equal(guard.peers.size, 1);
});

test('кэши ограничены, размер исходящего трафика учитывается до рассылки', () => {
  const guard = new DiscoveryGuard({ maxEntries: 2, peerBytes: 30 });
  assert.equal(guard.allow(mdns, packet(1), 'A', 2), false);
  assert.equal(guard.allow(mdns, packet(2), 'B', 1), true);
  assert.equal(guard.allow(mdns, packet(3), 'C', 1), false);
  for (let i = 0; i < 30; i++) guard.allow(mdns, packet(i), 'B', 1);
  assert.ok(guard.recent.size <= 2 && guard.peers.size <= 2);
});

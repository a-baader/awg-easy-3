'use strict';

// Одноразовый диагностический peer. Работает в отдельном контейнере, без host network.
const fs = require('node:fs/promises');
const net = require('node:net');
const dns = require('node:dns/promises');
const dgram = require('node:dgram');
const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const { UserspaceWireGuard } = require('./lib/UserspaceWireGuard');
const { runProcess } = require('./lib/ProcessRunner');
let driver;
const route = async address => {
  const family = net.isIP(address);
  if (!family) throw new Error('Некорректный адрес проверки');
  await runProcess('ip', ['-' + family, 'route', 'replace', address + (family === 4 ? '/32' : '/128'), 'dev', 'probe0']);
};
const request = async (value, tunnel) => {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search) throw new Error('URL проверки должен быть публичным и не содержать credentials или query');
  const host = url.hostname.replace(/^\[|\]$/g, '');
  const resolved = net.isIP(host) ? { address: host, family: net.isIP(host) } : await dns.lookup(host);
  if (tunnel) await route(resolved.address);
  return new Promise(resolve => {
    const req = (url.protocol === 'https:' ? https : http).request(url, {
      method: 'GET', lookup: (_name, _options, done) => done(null, resolved.address, resolved.family),
    }, response => { response.resume(); resolve({ reachable: true, status: response.statusCode }); });
    req.on('error', () => resolve({ reachable: false }));
    req.setTimeout(2500, () => req.destroy()); req.end();
  });
};
const dnsProbe = async address => {
  await route(address);
  const id = crypto.randomInt(0, 65536), header = Buffer.alloc(12); header.writeUInt16BE(id); header.writeUInt16BE(0x100, 2); header.writeUInt16BE(1, 4);
  const labels = Buffer.concat('example.com'.split('.').map(label => Buffer.concat([Buffer.from([label.length]), Buffer.from(label)])));
  const packet = Buffer.concat([header, labels, Buffer.from([0, 0, 1, 0, 1])]);
  const socket = dgram.createSocket(net.isIP(address) === 6 ? 'udp6' : 'udp4');
  return new Promise(resolve => {
    let finished = false;
    const done = value => { if (finished) return; finished = true; clearTimeout(timer); try { socket.close(); } catch {} resolve(value); };
    const timer = setTimeout(() => done(false), 2500);
    socket.once('error', () => done(false));
    socket.once('message', message => done(message.length >= 12 && message.readUInt16BE(0) === id && Boolean(message[2] & 0x80) && (message[3] & 15) === 0 && message.readUInt16BE(6) > 0));
    socket.send(packet, 53, address);
  });
};
(async () => {
  process.stdin.setEncoding('utf8'); let input = '';
  for await (const chunk of process.stdin) { input += chunk; if (input.length > 16384) throw new Error('Слишком большой запрос'); }
  const options = JSON.parse(input), tunnel = typeof options.nativeConfig === 'string';
  if (tunnel) {
    const binary = process.env.VPN_BACKEND === 'wireguard' ? 'wg' : 'awg';
    await fs.writeFile('/tmp/probe.conf', options.nativeConfig, { mode: 0o600 });
    const stripped = await runProcess(binary + '-quick', ['strip', '/tmp/probe.conf']);
    await fs.writeFile('/tmp/probe.stripped', stripped.stdout, { mode: 0o600 });
    driver = new UserspaceWireGuard({ binary, engine: binary === 'wg' ? 'wireguard-go' : 'amneziawg-go' });
    await driver.up({ interfaceName: 'probe0', serverConfig: options.nativeConfig, strippedConfigPath: '/tmp/probe.stripped' });
    // Mark отделяет внешний UDP от внутренних запросов, включая hairpin к endpoint.
    for (const family of [4, 6]) {
      const routes = JSON.parse((await runProcess('ip', ['-' + family, '-j', 'route', 'show', 'default'])).stdout);
      const underlay = routes.find(item => item.gateway && item.dev);
      if (underlay) {
        await runProcess('ip', ['-' + family, 'route', 'add', 'default', 'via', underlay.gateway, 'dev', underlay.dev, 'onlink', 'table', '100']);
        await runProcess('ip', ['-' + family, 'rule', 'add', 'fwmark', '51820', 'table', '100', 'priority', '100']);
      }
    }
    await runProcess(binary, ['set', 'probe0', 'fwmark', '51820']);
  }
  const results = [];
  for (const item of options.requests ?? []) {
    const response = await request(item.url, tunnel);
    results.push({ name: item.name, ...response, expected: item.allow, matches: item.allow ? response.reachable && response.status === 200 : !response.reachable });
  }
  let dnsResult;
  if (tunnel && options.dns) {
    const addresses = options.nativeConfig.match(/^DNS\s*=\s*(.+)$/m)?.[1].split(',').map(value => value.trim()) ?? [];
    dnsResult = await dnsProbe(addresses.find(value => net.isIP(value) === 4));
  }
  const packetProof = results.some(result => result.reachable) || dnsResult === true;
  process.stdout.write(JSON.stringify({ results, dns: dnsResult, packet_proof: packetProof }) + '\n');
})().catch(() => { console.error('Не удалось выполнить пакетную проверку VPN'); process.exitCode = 1; }).finally(async () => {
  if (driver?.child) { try { await driver.down('probe0'); } catch { process.exitCode = 1; } }
});

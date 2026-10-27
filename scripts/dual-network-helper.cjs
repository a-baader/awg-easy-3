'use strict';

const fs = require('node:fs/promises');
const http = require('node:http');
const dgram = require('node:dgram');
const assert = require('node:assert/strict');
const { Application } = require('/app/lib/Application');
const { UserspaceWireGuard } = require('/app/lib/UserspaceWireGuard');
const { runProcess: run } = require('/app/lib/ProcessRunner');
const mode = process.argv[2];
const kind = process.env.VPN_BACKEND;
const n = kind === 'wireguard' ? 9 : 8;
const port = kind === 'wireguard' ? 51825 : 51821;
const echo = () => http.createServer((req, res) => req.pipe(res)).listen(8080, '::');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const read = file => fs.readFile('/data/' + file, 'utf8');
const save = (file, value) => fs.writeFile('/data/' + file, value, { mode: 0o600 });
async function request(host, portNumber, allow) {
  try {
    const payload = Buffer.alloc(16384, 0x5a);
    const received = await new Promise((resolve, reject) => {
      const panel = portNumber === 51821 || portNumber === 51825;
      const req = http.request({ host, port: portNumber, path: panel ? '/api/v1/session' : '/', method: panel ? 'GET' : 'POST' }, res => {
        const chunks = []; res.on('data', c => chunks.push(c)); res.on('end', () => resolve(Buffer.concat(chunks))); res.on('error', reject);
      });
      const timer = setTimeout(() => req.destroy(Object.assign(new Error('Истёк срок ожидания'), { code: 'ETIMEDOUT' })), allow ? 10000 : 650);
      req.on('close', () => clearTimeout(timer)); req.on('error', reject); req.end(panel ? undefined : payload);
    });
    assert(allow, 'Запрещённый трафик доставлен');
    if (![51821, 51825].includes(portNumber)) assert(received.equals(payload));
  } catch (error) {
    if (allow || !['ETIMEDOUT', 'ECONNRESET', 'EHOSTUNREACH', 'ENETUNREACH', 'ECONNREFUSED'].includes(error.code)) throw error;
  }
  console.log('PASS: ' + (allow ? 'разрешено ' : 'запрещено ') + host + ':' + portNumber);
}
(async () => {
  if (mode === 'hold') return setInterval(() => {}, 1000);
  if (mode === 'wan') {
    await run('ip', ['address', 'add', '93.184.216.34/32', 'dev', 'lo']);
    await run('ip', ['-6', 'address', 'add', '2001:4860:dead::34/128', 'dev', 'lo', 'nodad']);
    echo();
    for (const udpPort of [5353, 1900]) {
      const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
      socket.on('message', message => {
        if (message.toString().includes('DUAL-TEST-')) fs.appendFile('/tmp/discovery.txt', message.toString() + '\n').catch(() => {});
      });
      await new Promise(resolve => socket.bind(udpPort, '0.0.0.0', resolve));
    }
    console.log('WAN готов.'); return;
  }
  if (mode === 'server') {
    const available = !await fs.stat('/data/geo-unavailable').then(() => true, () => false);
    const snapshot = available ? { database: { RU: { ipv4: ['93.184.216.34/32'], ipv6: ['2001:4860:dead::34/128'] }, ZZ: { ipv4: [], ipv6: [] } }, countries: ['RU', 'ZZ'] } : null;
    const app = new Application({ backendName: kind, dualMode: true, geoFactory: () => ({ current: snapshot,
      load: async () => snapshot, refresh: async () => null, start() {}, stop: async () => {} }) });
    if (!await app.store.load()) {
      const initial = await app.initialize({ ipv6: { mode: 'nat66', subnet: `fd42:${n}::/64`, serverAddress: `fd42:${n}::1`, firstClientAddress: `fd42:${n}::2` } });
      await save('test-password', initial.bootstrapPassword);
    }
    await app.backend.validateEnvironment(); await app.start();
    const state = await app.store.load();
    if (state.clients.length === 1) {
      await app.clientManager.createClient({ name: 'Home B', networkGroup: 'home' });
      await app.clientManager.createClient({ name: 'Guest', networkGroup: 'guest' });
    }
    const readyState = await app.store.load();
    const artifacts = app.artifactBuilder({ ...readyState, geoDatabase: snapshot?.database ?? {} });
    for (const [index, client] of readyState.clients.entries()) await save('profile-' + index + '.conf', artifacts.clientArtifacts[client.id].nativeConfig);
    console.log('Сервер готов.');
    process.once('SIGTERM', () => app.stop().then(() => process.exit(0)).catch(() => process.exit(1)));
    return;
  }
  if (mode === 'policy') {
    const state = JSON.parse(await read('state.json'));
    const target = state.clients.find(client => client.name === process.argv[3] || client.id === process.argv[3]);
    assert(target, 'Клиент для проверки не найден');
    const base = `http://10.${n}.0.1:${port}`;
    const login = await fetch(base + '/api/v1/session', { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: base }, body: JSON.stringify({ password: await read('test-password') }) });
    assert.equal(login.status, 200);
    const response = await fetch(base + '/api/v1/clients/' + target.id, { method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Origin: base, Cookie: login.headers.get('set-cookie').split(';')[0] }, body: process.argv[4] });
    assert.equal(response.status, 200, 'Изменение политики не принято');
    console.log('Политика изменена'); return;
  }
  if (mode === 'host-echo') { http.createServer((req, res) => req.pipe(res)).listen(18080, '::'); console.log('Host echo готов.'); return; }
  if (mode === 'flow') {
    const agent = new http.Agent({ keepAlive: true, maxSockets: 1 }); let busy = false, count = 0;
    await fs.writeFile('/tmp/flow-count', '0');
    setInterval(() => {
      if (busy) return; busy = true;
      const req = http.request({ host: '93.184.216.34', port: 8080, method: 'POST', agent }, response => {
        const chunks = []; response.on('data', chunk => chunks.push(chunk));
        response.on('end', () => {
          if (Buffer.concat(chunks).equals(Buffer.alloc(4096, 0x5a))) fs.writeFile('/tmp/flow-count', String(++count)).catch(() => {});
        });
      });
      req.on('error', () => {}); req.on('close', () => { busy = false; });
      req.setTimeout(750, () => req.destroy()); req.end(Buffer.alloc(4096, 0x5a));
    }, 200);
    console.log('Поток запущен'); return;
  }
  if (mode === 'flow-count') { console.log(await fs.readFile('/tmp/flow-count', 'utf8')); return; }
  if (mode === 'client') {
    const index = Number(process.argv[3]);
    const config = await read('profile-' + index + '.conf');
    const binary = kind === 'wireguard' ? 'wg' : 'awg';
    await fs.writeFile('/tmp/peer.conf', config, { mode: 0o600 });
    const stripped = await run(binary + '-quick', ['strip', '/tmp/peer.conf']);
    await fs.writeFile('/tmp/peer.stripped', stripped.stdout, { mode: 0o600 });
    const own4 = config.match(/^Address = ([^,\n]+)/m)[1].split('/')[0];
    const own6 = config.match(/^Address = .*,\s*([^,\n]+)/m)[1].split('/')[0];
    let driver;
    if (kind === 'wireguard') {
      driver = new UserspaceWireGuard();
      await driver.up({ interfaceName: 'peer0', serverConfig: config, strippedConfigPath: '/tmp/peer.stripped' });
    } else {
      await run('amneziawg-go', ['peer0']);
      await run('awg', ['setconf', 'peer0', '/tmp/peer.stripped']);
      await run('ip', ['address', 'add', own4 + '/32', 'dev', 'peer0']);
      await run('ip', ['-6', 'address', 'add', own6 + '/128', 'dev', 'peer0', 'nodad']);
      await run('ip', ['link', 'set', 'dev', 'peer0', 'mtu', '1280', 'up']);
    }
    const defaults = JSON.parse((await run('ip', ['-j', '-4', 'route', 'show', 'default'])).stdout);
    const underlay = defaults.find(item => item.gateway && item.dev);
    assert(underlay, 'Не найден внешний маршрут клиента');
    await run('ip', ['route', 'add', 'default', 'via', underlay.gateway, 'dev', underlay.dev, 'table', '100']);
    await run('ip', ['rule', 'add', 'fwmark', '51820', 'table', '100', 'priority', '100']);
    await run(binary, ['set', 'peer0', 'fwmark', '51820']);
    for (const subnet of ['0.0.0.0/1', '128.0.0.0/1', '224.0.0.0/4']) await run('ip', ['route', 'add', subnet, 'dev', 'peer0']);
    for (const subnet of ['2000::/3', 'fc00::/7']) await run('ip', ['-6', 'route', 'add', subnet, 'dev', 'peer0']);
    echo();
    for (const udpPort of [5353, 1900]) {
      const socket = dgram.createSocket({ type: 'udp4', reuseAddr: true });
      socket.on('message', message => {
        if (message.toString().includes('DUAL-TEST-')) fs.appendFile('/tmp/discovery.txt', message.toString() + '\n').catch(() => {});
      });
      await new Promise(resolve => socket.bind(udpPort, '0.0.0.0', resolve));
    }
    console.log('Клиент готов.'); return;
  }
  if (mode === 'traffic') return request(process.argv[3], Number(process.argv[4]), process.argv[5] === 'allow');
  if (mode === 'discover-send') {
    const socket = dgram.createSocket('udp4');
    const tag = process.argv[3], udpPort = Number(process.argv[4]);
    await new Promise((resolve, reject) => socket.bind(0, process.argv[5] || `10.${n}.0.2`, () => {
      socket.send(Buffer.from('DUAL-TEST-' + tag), udpPort, `10.${n}.0.1`, error => error ? reject(error) : resolve());
    }));
    socket.close(); return;
  }
  if (mode === 'discover-count') {
    let body = ''; try { body = await fs.readFile('/tmp/discovery.txt', 'utf8'); } catch {}
    console.log(body.split('\n').filter(line => line.includes('DUAL-TEST-' + process.argv[3])).length); return;
  }
})().catch(error => {
  console.error('Ошибка сетевого теста (' + mode + '): ' + String(error.message).replace(/[A-Za-z0-9+/]{43}=/g, '[КЛЮЧ СКРЫТ]'));
  process.exitCode = 1;
});

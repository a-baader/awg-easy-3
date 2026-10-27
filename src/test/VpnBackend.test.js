'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createBackend } = require('../lib/VpnBackend');
const { UserspaceWireGuard } = require('../lib/UserspaceWireGuard');
const { BootstrapInstaller } = require('../lib/BootstrapInstaller');
const { buildAwgArtifacts } = require('../lib/AwgArtifacts');
const { validateState } = require('../lib/StateStore');
const { ClientManager } = require('../lib/ClientManager');
const { ApiService } = require('../lib/ApiService');
const { HttpServer } = require('../lib/HttpServer');
const path = require('node:path');
const key = n => Buffer.alloc(32, n).toString('base64');

async function fixture() {
  let state = null;
  const store = { load: async () => state, save: async input => (state = validateState(input)) };
  const backend = createBackend('wireguard');
  backend.keyManager = { generateKeyPair: async () => ({ privateKey: key(1), publicKey: key(2) }),
    generatePeerKeys: async () => ({ privateKey: key(3), publicKey: key(4), presharedKey: key(5) }) };
  await new BootstrapInstaller({ backend, store, passwordHasher: async () => 'hash',
    networkDetector: { detect: async () => ({ endpointCandidate: '192.0.2.1', wanInterface: 'eth0', ipv6: { available: false } }) } }).install({});
  return { store, backend, state: () => state };
}

test('WG bootstrap создаёт отдельную схему и стандартный конфиг, AWG-поля запрещены', async () => {
  const f = await fixture(); const state = f.state();
  assert.equal(state.version, 3);
  assert.equal(state.server.backend, 'wireguard');
  assert.equal(state.server.profile, undefined);
  assert.equal(state.server.interfaceName, 'wg0');
  assert.equal(state.server.listenPort, 51824);
  assert.equal(state.server.panelPort, 51825);
  assert.equal(state.clients[0].address4, '10.9.0.2');
  const artifacts = buildAwgArtifacts(state);
  const config = artifacts.clientArtifacts['home-admin'].nativeConfig;
  assert.match(config, /Endpoint = 192\.0\.2\.1:51824/);
  assert.match(config, /PersistentKeepalive = 25\n/);
  assert.doesNotMatch(config, /HeaderProtection|Jc =|H1 =|RandomTrailers|DisableCookies/);
  assert.equal(artifacts.clientArtifacts['home-admin'].vpnLink, null);
  assert.match(artifacts.nftables, /table inet wg_easy_3/);
  assert.throws(() => validateState({ ...state, version: 1 }), /Unsupported/);
  assert.throws(() => validateState({ ...state, server: { ...state.server, profile: {} } }), /AWG/);
  assert.throws(() => validateState({ ...state, clients: state.clients.map(c => ({ ...c, persistentKeepalive: '25-35' })) }), /Keepalive/);
  assert.throws(() => createBackend().assertState(state), /не соответствует/);
});

test('WG lifecycle использует общий ClientManager, экспорт conf/QR авторизован', async () => {
  const f = await fixture(); let applied;
  const manager = new ClientManager({ store: f.store, keyManager: {
    generatePeerKeys: async () => ({ privateKey: key(6), publicKey: key(7), presharedKey: key(8) }) },
    applier: { apply: async options => { applied = options; } }, idGenerator: () => 'phone' });
  const created = await manager.createClient({ name: 'Телефон', networkGroup: 'guest' });
  assert.equal(created.client.address4, '10.9.0.3');
  assert.match(applied.serverConfig, /ListenPort = 51824/);
  const service = new ApiService({ store: f.store, clientManager: manager,
    passwordManager: { verify() {}, changePassword() {} }, sessionManager: { create() {}, verify: async token => token === 'ok' },
    qrGenerator: async text => 'QR:' + text });
  await assert.rejects(service.exportClient('no', 'phone', 'native-config'), e => e.statusCode === 401);
  const native = await service.exportClient('ok', 'phone', 'native-config');
  assert.equal((await service.capabilities()).backend, 'wireguard');
  assert.equal((await service.exportClient('ok', 'phone', 'qr-native-svg')).value, 'QR:' + native.value);
  assert.equal((await service.exportClient('ok', 'phone', 'qr-svg')).value, 'QR:' + native.value);
  await assert.rejects(service.exportClient('ok', 'phone', 'vpn-link'), e => e.statusCode === 400);
  await manager.updateClient('phone', { enabled: false });
  assert(!applied.serverConfig.includes(key(7)));
  await manager.deleteClient('phone');
  assert.equal(f.state().clients.length, 1);
});

test('userspace WG запускает официальный foreground engine, без kernel fallback', async () => {
  const calls = []; let alive = false;
  const runner = async (file, args) => {
    calls.push([file, ...args]);
    if (file === 'ip' && args[0] === 'link' && args[1] === 'show' && !alive) throw Object.assign(new Error('Нет'), { code: 1 });
    return { stdout: '' };
  };
  const spawnProcess = (file, args) => {
    calls.push([file, ...args]); alive = true;
    const child = new EventEmitter(); Object.assign(child, { pid: 123, exitCode: null, signalCode: null });
    child.kill = signal => { alive = false; child.signalCode = signal; child.emit('exit'); return true; };
    return child;
  };
  const driver = new UserspaceWireGuard({ runner, spawnProcess });
  await driver.up({ interfaceName: 'wg0', serverConfig: '[Interface]\nPrivateKey = TEST\nAddress = 10.9.0.1/24\n\n[Peer]\nPublicKey = TEST\n', strippedConfigPath: '/tmp/test-only' });
  assert(calls.some(c => c.join(' ') === 'wireguard-go --foreground wg0'));
  assert(calls.some(c => c.join(' ') === 'wg setconf wg0 /tmp/test-only'));
  assert(!calls.some(c => c.includes('wireguard') || c[0] === 'wg-quick'));
  await driver.down('wg0'); assert.equal(alive, false);
});

test('WG tools проверяются по происхождению, а не по имени wg', async () => {
  await assert.rejects(createBackend('wireguard', { runner: async () => ({ stdout: 'amneziawg-tools v3.1' }) }).validateEnvironment(), /несовместимые/);
  const caps = await createBackend('wireguard', { runner: async (file) => ({ stdout: file === 'wg' ? 'wireguard-tools v1.0.20250521' : 'wireguard-go 0.0.20250522' }) }).validateEnvironment();
  assert.equal(caps.vpnUrl, false); assert.equal(caps.nativeQr, true);
});

test('WG HTTP metadata идентифицирует свою панель, экспорт без сессии закрыт', async t => {
  const f = await fixture();
  const service = new ApiService({ store: f.store, clientManager: { createClient() {} },
    passwordManager: { verify() {}, changePassword() {} }, sessionManager: { create() {}, verify: async () => false } });
  const server = new HttpServer({ api: service, publicDirectory: path.join(__dirname, '../www') });
  const address = await server.listen({ host: '127.0.0.1', port: 0 });
  t.after(() => server.close());
  const root = `http://127.0.0.1:${address.port}`;
  const caps = await (await fetch(root + '/api/v1/capabilities')).json();
  assert.equal(caps.backend, 'wireguard'); assert.equal(caps.vpnUrl, false);
  assert.equal((await (await fetch(root + '/manifest.json')).json()).name, 'WG Easy Dual');
  const response = await fetch(root + '/api/v1/clients/home-admin/export?format=native-config');
  assert.equal(response.status, 401);
});

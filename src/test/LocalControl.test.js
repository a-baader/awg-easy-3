'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { LocalControl } = require('../lib/LocalControl');
const { StateStore } = require('../lib/StateStore');
const { PasswordManager } = require('../lib/PasswordManager');
const { SessionManager } = require('../lib/SessionManager');
const { ApiService } = require('../lib/ApiService');
const { ClientManager } = require('../lib/ClientManager');
const { generateOfficialProfile } = require('../lib/Awg3Config');

test('локальный control socket имеет 0600, разделяет сервисный слой и не раскрывает исключения', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'dual-control-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const client = { id: 'one', name: 'Телефон', address4: '10.9.0.2', enabled: true, networkGroup: 'home', privateKey: 'SECRET' };
  const changes = [];
  const control = new LocalControl({ socketPath: path.join(directory, 'control.sock'),
    store: { load: async () => ({ clients: [client] }) },
    backend: { capabilities: () => ({ vpnUrl: false }) },
    clientManager: { updateClient: async (id, value) => { changes.push([id, value]); return { client: { ...client, ...value } }; },
      getClientExport: async () => ({ nativeConfig: '[Interface]\nPrivateKey = TEST\n', vpnLink: null }),
      createClient: async () => { throw new Error('SECRET_PRIVATE'); } } });
  await control.start(); t.after(() => control.close());
  assert.equal((await fs.stat(control.socketPath)).mode & 0o777, 0o600);
  const rpc = value => new Promise((resolve, reject) => {
    const socket = net.connect(control.socketPath); let response = '';
    socket.on('connect', () => socket.write(JSON.stringify(value) + '\n'));
    socket.on('data', data => { response += data; }); socket.on('error', reject);
    socket.on('end', () => resolve(JSON.parse(response)));
  });
  const listed = await rpc({ operation: 'client.list' });
  assert.equal(listed.value[0].name, 'Телефон'); assert(!JSON.stringify(listed).includes('SECRET'));
  assert.equal((await rpc({ operation: 'client.disable', args: { name: 'Телефон' } })).value.enabled, false);
  assert.deepEqual(changes, [['one', { enabled: false }]]);
  assert.equal((await rpc({ operation: 'client.export', args: { name: 'Телефон', format: 'vpn-url' } })).code, 'UNSUPPORTED_FORMAT');
  const failed = await rpc({ operation: 'client.add', args: { name: 'X' } });
  assert.equal(failed.ok, false); assert(!JSON.stringify(failed).includes('SECRET_PRIVATE'));
});

test('общая очередь StateStore исключает пересечение изменений клиентов и auth', async () => {
  const store = new StateStore('/tmp/not-used-control-state.json');
  const events = []; let release;
  const first = store.serialize(async () => { events.push('client-start'); await new Promise(resolve => { release = resolve; }); events.push('client-end'); });
  await new Promise(resolve => setImmediate(resolve));
  const second = store.serialize(async () => { events.push('auth'); });
  assert.deepEqual(events, ['client-start']); release(); await Promise.all([first, second]);
  assert.deepEqual(events, ['client-start', 'client-end', 'auth']);
});

test('режим обслуживания дожидается записи и отклоняет следующие изменения клиентов и пароля', async () => {
  const store = new StateStore('/tmp/not-used-maintenance.json');
  let maintenance = false, release;
  const manager = new ClientManager({ store, applier: { apply() {} }, isReadOnly: () => maintenance });
  const passwords = new PasswordManager({ store, isReadOnly: () => maintenance });
  const events = [];
  const first = manager.serialize(async () => {
    events.push('начало');
    await new Promise(resolve => { release = resolve; });
    events.push('записано');
  });
  await new Promise(resolve => setImmediate(resolve));
  const control = new LocalControl({ setMaintenance: value => store.serialize(() => { maintenance = value; }) });
  const freeze = control.execute({ operation: 'maintenance.begin' });
  const mutation = manager.serialize(() => events.push('потерянная запись'));
  const password = passwords.resetPassword('new-password-for-test');
  const rejected = [assert.rejects(mutation, { code: 'MAINTENANCE', statusCode: 503 }),
    assert.rejects(password, { code: 'MAINTENANCE', statusCode: 503 })];
  assert.equal(maintenance, false);
  release();await Promise.all([first, freeze, ...rejected]);
  assert.deepEqual(events, ['начало', 'записано']);
  await control.execute({ operation: 'maintenance.end' });
  await manager.serialize(() => events.push('после обслуживания'));
  assert.equal(events.at(-1), 'после обслуживания');
});

test('вход со старым паролем не получает действующую сессию после конкурентного reset', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'dual-auth-race-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const store = new StateStore(path.join(directory, 'state.json'));
  await store.save({ version: 1, auth: { passwordHash: 'old-hash', sessionSecret: 'old-test-session' },
    server: { interfaceName: 'awg0', wanInterface: 'eth0', privateKey: 'test', publicKey: 'test', address4: '10.8.0.1', ipv4Subnet: '10.8.0.0/24', endpointHost: 'vpn.example.com', listenPort: 51820, panelPort: 51821, profile: generateOfficialProfile() },
    clients: [{ id: 'admin', name: 'admin', enabled: true, networkGroup: 'home', address4: '10.8.0.2', privateKey: 'test', publicKey: 'test' }] });
  let release, entered;
  const comparing = new Promise(resolve => { entered = resolve; });
  const passwords = new PasswordManager({ store, compare: async () => { entered(); await new Promise(resolve => { release = resolve; }); return true; }, hash: async () => 'new-hash' });
  const sessions = new SessionManager({ store });
  const service = new ApiService({ store, passwordManager: passwords, sessionManager: sessions, clientManager: { createClient() {} } });
  const login = service.login('old-test-password');
  await comparing;
  const reset = passwords.resetPassword('new-test-password-123');
  release();
  const result = await login; await reset;
  const token = result.cookie.split(';')[0].split('=')[1];
  assert.equal(await sessions.verify(token), false);
  assert.equal((await store.load()).auth.passwordHash, 'new-hash');
});

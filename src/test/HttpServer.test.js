'use strict';

const assert = require('node:assert/strict');
const http = require('node:http');
const test = require('node:test');

const { HttpServer } = require('../lib/HttpServer');
const { ApiService } = require('../lib/ApiService');
const { SessionManager } = require('../lib/SessionManager');
const { LoginLimiter } = require('../lib/LoginLimiter');

const request = (port, method, pathname, { body, cookie, headers = {} } = {}) => new Promise((resolve, reject) => {
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const outgoing = http.request({
    host: '127.0.0.1', port, method, path: pathname,
    headers: {
      ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
      ...(cookie ? { Cookie: cookie } : {}),
      ...headers,
    },
  }, (response) => {
    const chunks = [];
    response.on('data', (chunk) => chunks.push(chunk));
    response.on('end', () => resolve({
      body: Buffer.concat(chunks).toString('utf8'),
      headers: response.headers,
      status: response.statusCode,
    }));
  });
  outgoing.on('error', reject);
  if (payload) outgoing.write(payload);
  outgoing.end();
});

const fixture = async (t) => {
  const calls = [];
  const api = {
    session: async (token) => ({ authenticated: token === 'valid' }),
    login: async (password) => {
      calls.push(['login', password]);
      return { cookie: 'awg_easy_3_session=valid; HttpOnly' };
    },
    logout: () => ({ cookie: 'awg_easy_3_session=; Max-Age=0' }),
    listClients: async (token) => {
      calls.push(['list', token]);
      return [{ id: 'phone', name: 'Phone' }];
    },
    createClient: async (token, input) => ({ client: { id: 'new', ...input }, vpnLink: 'vpn://share' }),
    updateClient: async (token, id, input) => ({ id, ...input }),
    deleteClient: async () => ({ success: true }),
    exportClient: async (token, id, format) => ({
      contentType: format === 'native-config' ? 'application/octet-stream' : 'text/plain; charset=utf-8',
      ...(format === 'native-config' ? { downloadName: 'Honor_50.conf' } : {}),
      value: format === 'native-config' ? '[Interface]' : 'vpn://share',
    }),
    changePassword: async () => ({ cookie: 'awg_easy_3_session=; Max-Age=0' }),
  };
  const server = new HttpServer({ api });
  const address = await server.listen({ host: '127.0.0.1', port: 0 });
  t.after(() => server.close());
  return { calls, port: address.port };
};

test('HTTP logout отзывает скопированную cookie на стороне сервера', async t => {
  let state = { auth: { sessionSecret: 'Тестовый секрет' }, server: { uiLanguage: 'ru' } };
  const store = { load: async () => state, save: async next => { state = next; } };
  const api = new ApiService({ store, sessionManager: new SessionManager({ store }),
    passwordManager: { verify: async password => password === 'Тестовый пароль', changePassword() {} },
    clientManager: { createClient() {} } });
  const server = new HttpServer({ api });
  const { port } = await server.listen({ host: '127.0.0.1', port: 0 });
  t.after(() => server.close());
  const login = await request(port, 'POST', '/api/v1/session', { body: { password: 'Тестовый пароль' } });
  assert.equal(login.status, 200);
  const cookie = login.headers['set-cookie'][0].split(';')[0];
  assert.equal(JSON.parse((await request(port, 'GET', '/api/v1/session', { cookie })).body).authenticated, true);
  assert.equal((await request(port, 'DELETE', '/api/v1/session', { cookie })).status, 200);
  assert.equal(JSON.parse((await request(port, 'GET', '/api/v1/session', { cookie })).body).authenticated, false);
  assert.equal((await request(port, 'GET', '/api/v1/clients', { cookie })).status, 401);
});

test('HTTP login возвращает 429 и Retry-After; подмена X-Forwarded-For не обходит бюджет', async t => {
  let comparisons = 0;
  const store = { load: async () => ({ auth: { passwordHash: 'тест', sessionSecret: 'тестовый секрет' } }) };
  const api = new ApiService({ store, loginLimiter: new LoginLimiter({ peerBurst: 2, clock: () => 0 }),
    passwordManager: { verify: async () => { comparisons += 1; return false; }, changePassword() {} },
    sessionManager: { create: () => assert.fail('Неверный пароль не выдаёт сессию'), verify: async () => false },
    clientManager: { createClient() {} } });
  const server = new HttpServer({ api });
  const { port } = await server.listen({ host: '127.0.0.1', port: 0 });
  t.after(() => server.close());
  for (const address of ['10.8.0.2', '10.8.0.3']) {
    assert.equal((await request(port, 'POST', '/api/v1/session', {
      body: { password: 'неверный пароль' }, headers: { 'X-Forwarded-For': address },
    })).status, 401);
  }
  const blocked = await request(port, 'POST', '/api/v1/session', {
    body: { password: 'неверный пароль' }, headers: { 'X-Forwarded-For': '10.8.0.4' },
  });
  assert.equal(blocked.status, 429);
  assert.equal(blocked.headers['retry-after'], '12');
  assert.equal(comparisons, 2);
  assert.equal((await request(port, 'GET', '/api/v1/session')).status, 200);
});

test('serves versioned session and client routes with security headers', async (t) => {
  const { calls, port } = await fixture(t);
  const login = await request(port, 'POST', '/api/v1/session', { body: { password: 'secret' } });
  assert.equal(login.status, 200);
  assert.match(login.headers['set-cookie'][0], /HttpOnly/);
  const clients = await request(port, 'GET', '/api/v1/clients', { cookie: 'awg_easy_3_session=valid' });
  assert.equal(clients.status, 200);
  assert.match(clients.headers['content-security-policy'], /frame-ancestors 'none'/);
  assert.deepEqual(JSON.parse(clients.body), [{ id: 'phone', name: 'Phone' }]);
  assert.deepEqual(calls, [['login', 'secret'], ['list', 'valid']]);
});

test('supports policy mutation and explicit export routes', async (t) => {
  const { port } = await fixture(t);
  const updated = await request(port, 'PATCH', '/api/v1/clients/phone', {
    body: { networkGroup: 'home' },
    cookie: 'awg_easy_3_session=valid',
  });
  assert.deepEqual(JSON.parse(updated.body), { id: 'phone', networkGroup: 'home' });
  const exported = await request(port, 'GET', '/api/v1/clients/phone/export?format=vpn-link', {
    cookie: 'awg_easy_3_session=valid',
  });
  assert.equal(exported.status, 200);
  assert.equal(exported.body, 'vpn://share');
  const native = await request(port, 'GET', '/api/v1/clients/phone/export?format=native-config', {
    cookie: 'awg_easy_3_session=valid',
  });
  assert.match(native.headers['content-disposition'], /filename="Honor_50\.conf"/);
  assert.match(native.headers['content-disposition'], /filename\*=UTF-8''Honor_50\.conf/);
  assert.equal(native.headers['content-type'], 'application/octet-stream');
  assert.equal(native.body, '[Interface]');
  assert.equal(native.headers['cache-control'], 'no-store');
  assert.equal(native.headers['x-content-type-options'], 'nosniff');
  assert.equal(exported.headers['content-type'], 'text/plain; charset=utf-8');
});

test('serves an origin-relative web-app manifest without caching credentials or embedding a host', async (t) => {
  const { port } = await fixture(t);
  const response = await request(port, 'GET', '/manifest.json?v=20260909-1');
  assert.equal(response.status, 200);
  assert.equal(response.headers['content-type'], 'application/manifest+json; charset=utf-8');
  assert.equal(response.headers['cache-control'], 'no-cache');
  assert.equal(response.headers['set-cookie'], undefined);
  const manifest = JSON.parse(response.body);
  assert.equal(manifest.id, '/');
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/');
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.prefer_related_applications, false);
  for (const icon of manifest.icons) {
    const asset = await request(port, 'HEAD', `/${icon.src}`);
    assert.equal(asset.status, 200);
    assert.equal(asset.headers['content-type'], 'image/png');
  }
});

test('rejects cross-site mutations, oversized bodies and legacy endpoints', async (t) => {
  const { port } = await fixture(t);
  const crossSite = await request(port, 'POST', '/api/v1/session', {
    body: { password: 'secret' }, headers: { 'Sec-Fetch-Site': 'cross-site' },
  });
  assert.equal(crossSite.status, 403);
  const oversized = await request(port, 'POST', '/api/v1/session', {
    body: { password: 'x'.repeat(17 * 1024) },
  });
  assert.equal(oversized.status, 413);
  assert.equal((await request(port, 'GET', '/api/wireguard/backup')).status, 404);
});

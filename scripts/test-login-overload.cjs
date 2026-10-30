'use strict';

// Запускается в production-образе с network none; VPN и данные хоста не меняет.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const bcrypt = require('/app/node_modules/bcryptjs');
const { ApiService } = require('/app/lib/ApiService');
const { HttpServer } = require('/app/lib/HttpServer');
const { PasswordManager } = require('/app/lib/PasswordManager');
const { SessionManager } = require('/app/lib/SessionManager');

(async () => {
  const password = crypto.randomBytes(24).toString('hex');
  let state = { auth: { passwordHash: await bcrypt.hash(password, 14),
    sessionSecret: crypto.randomBytes(48).toString('base64url') }, server: { uiLanguage: 'ru' }, clients: [] };
  let queue = Promise.resolve();
  const store = { load: async () => structuredClone(state), save: async next => { state = next; },
    serialize: operation => { const next = queue.then(operation); queue = next.catch(() => {}); return next; } };
  const api = new ApiService({ store, passwordManager: new PasswordManager({ store }),
    sessionManager: new SessionManager({ store }), clientManager: { createClient() {} } });
  const server = new HttpServer({ api });
  const address = await server.listen({ host: '127.0.0.1', port: 0 });
  const base = `http://127.0.0.1:${address.port}`;
  const login = value => fetch(base + '/api/v1/session', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: value }),
    signal: AbortSignal.timeout(10_000) });
  const pending = [];
  try {
    pending.push(login('неверный пароль'), login('неверный пароль'));
    const deadline = Date.now() + 3000;
    while (api.loginLimiter.active !== 2 && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
    assert.equal(api.loginLimiter.active, 2, 'Две проверки должны выполняться параллельно');
    let committed = false;
    await store.serialize(() => { committed = true; });
    assert(committed, 'Очередь управления не должна ждать bcrypt');
    const status = await fetch(base + '/api/v1/session');
    assert.equal(status.status, 200, 'HTTP должен отвечать во время bcrypt');
    assert(api.loginLimiter.active > 0, 'Проверка HTTP должна пройти до завершения bcrypt');
    const rejected = await login('третья попытка');
    assert.equal(rejected.status, 429, 'Третья параллельная проверка не должна попадать в очередь');
    assert.equal(rejected.headers.get('retry-after'), '1');
    for (const result of await Promise.all(pending)) assert.equal(result.status, 401);
    assert.equal(api.loginLimiter.active, 0);
    const accepted = await login(password);
    assert.equal(accepted.status, 200, 'После освобождения слотов правильный пароль должен работать');
    const cookie = accepted.headers.get('set-cookie').split(';')[0];
    assert.equal((await fetch(base + '/api/v1/clients', { headers: { Cookie: cookie } })).status, 200);
    assert.equal((await fetch(base + '/api/v1/session', { method: 'DELETE', headers: { Cookie: cookie } })).status, 200);
    assert.equal((await fetch(base + '/api/v1/clients', { headers: { Cookie: cookie } })).status, 401);
    console.log('PASS: настоящий bcrypt в production, HTTP и очередь управления доступны; перегрузка 429, вход и отзыв сессии работают');
  } finally {
    await Promise.allSettled(pending);
    server.server.closeAllConnections();
    await server.close();
  }
})().catch(error => { console.error('Ошибка проверки login: ' + error.message); process.exitCode = 1; });

'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const bcrypt = require('bcryptjs');
const { compareInWorker } = require('../lib/IsolatedPasswordCompare');
const { PasswordManager } = require('../lib/PasswordManager');

test('настоящий bcrypt проверяется в worker без блокирования event loop', async () => {
  const hash = await bcrypt.hash('Тестовый пароль', 10);
  let ticks = 0;
  const timer = setInterval(() => { ticks += 1; }, 5);
  try {
    assert.equal(await compareInWorker('Тестовый пароль', hash), true);
    assert.equal(await compareInWorker('Неверный пароль', hash), false);
    assert(ticks > 1, 'Основной event loop должен продолжать работу во время bcrypt');
  } finally {
    clearInterval(timer);
  }
});

test('превышение времени останавливает worker и возвращает общий отказ без секретов', async () => {
  const hash = await bcrypt.hash('Тестовый пароль', 4);
  await assert.rejects(compareInWorker('Тестовый пароль', hash, { timeoutMs: 1 }), error => {
    assert.equal(error.statusCode, 503);
    assert.equal(error.code, 'LOGIN_VERIFY_UNAVAILABLE');
    assert(!error.message.includes(hash));
    return true;
  });
  assert.equal(await compareInWorker('Тестовый пароль', hash), true);
});

test('PasswordManager проверяет выбранный снимок auth; слишком длинный ввод не достигает bcrypt', async () => {
  const hash = await bcrypt.hash('Тестовый пароль', 4);
  const store = { load: async () => ({ auth: { passwordHash: 'другой hash' } }), save() {} };
  const manager = new PasswordManager({ store });
  assert.equal(await manager.verify('Тестовый пароль', { auth: { passwordHash: hash }, isolated: true }), true);
  manager.compare = () => assert.fail('Слишком длинный ввод не должен проверяться');
  assert.equal(await manager.verify('я'.repeat(1024), { isolated: true }), false);
});

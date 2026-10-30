'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const { LoginLimiter } = require('../lib/LoginLimiter');

const limited = error => error.statusCode === 429 && Number.isInteger(error.retryAfter) && error.retryAfter > 0;

test('попытки ограничены по адресу; IPv4-mapped адрес не создаёт второй бюджет', async () => {
  let now = 0;
  const limiter = new LoginLimiter({ clock: () => now, peerBurst: 2 });
  await limiter.run('10.8.0.2', async () => {});
  await limiter.run('::ffff:10.8.0.2', async () => {});
  await assert.rejects(limiter.run('10.8.0.2', async () => assert.fail('Вызов не должен начаться')), limited);
  await limiter.run('10.8.0.3', async () => {});
  now = 12_000;
  await limiter.run('10.8.0.2', async () => {});
});

test('глобальный бюджет действует независимо от числа адресов', async () => {
  const limiter = new LoginLimiter({ globalBurst: 2, clock: () => 0 });
  for (const address of ['10.8.0.2', '10.8.0.3']) await limiter.run(address, async () => {});
  await assert.rejects(limiter.run('10.8.0.4', async () => {}), limited);
});

test('параллельность ограничена; очередь login не создаётся и слот возвращается после ошибки', async () => {
  let finish;
  const limiter = new LoginLimiter({ maxConcurrent: 1 });
  const first = limiter.run('10.8.0.2', () => new Promise(resolve => { finish = resolve; }));
  await assert.rejects(limiter.run('10.8.0.3', async () => assert.fail('Лишняя задача не должна запускаться')), limited);
  assert.equal(limiter.active, 1);
  finish(); await first;
  await assert.rejects(limiter.run('10.8.0.3', async () => { throw new Error('Тестовый отказ'); }), /Тестовый отказ/);
  assert.equal(limiter.active, 0);
  assert.equal(await limiter.run('10.8.0.3', async () => 'готово'), 'готово');
});

test('таблица адресов ограничена и освобождает только неактивные записи', async () => {
  let now = 0;
  const limiter = new LoginLimiter({ maxAddresses: 1, idleMs: 60_000, clock: () => now });
  await limiter.run('10.8.0.2', async () => {});
  await assert.rejects(limiter.run('10.8.0.3', async () => {}), limited);
  assert.equal(limiter.addresses.size, 1);
  now = 60_001;
  await limiter.run('10.8.0.3', async () => {});
  assert.equal(limiter.addresses.has('10.8.0.2'), false);
});

test('ошибочные адреса используют общий бюджет вместо роста таблицы', async () => {
  const limiter = new LoginLimiter({ peerBurst: 1, clock: () => 0 });
  await limiter.run(undefined, async () => {});
  await assert.rejects(limiter.run('не адрес', async () => {}), limited);
  assert.equal(limiter.addresses.size, 1);
});

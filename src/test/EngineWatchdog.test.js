'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EngineWatchdog } = require('../lib/EngineWatchdog');

test('контроль engine не перекрывает проверки и вызывает остановку ровно один раз', async () => {
  let finish, failures = 0, probes = 0, canceled = 0;
  const watcher = new EngineWatchdog({ probe: () => { probes++; return new Promise((_, reject) => { finish = reject; }); },
    onFailure: async () => { failures++; }, schedule: () => ({}), cancel: () => { canceled++; } });
  watcher.start();
  const pending = watcher.check();
  await watcher.check();
  assert.equal(probes, 1);
  finish(new Error('Движок завершился'));
  await pending; await watcher.check();
  assert.equal(failures, 1); assert.equal(canceled, 1);
});

test('штатный stop не вызывает аварийное завершение из проверки в полёте', async () => {
  let fail, failures = 0;
  const watcher = new EngineWatchdog({ probe: () => new Promise((_, reject) => { fail = reject; }),
    onFailure: async () => { failures++; }, schedule: () => ({}), cancel() {} });
  watcher.start(); const pending = watcher.check(); watcher.stop();
  fail(new Error('Остановлен штатно')); await pending;
  assert.equal(failures, 0);
});

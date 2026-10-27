'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { instanceConfig, validateInstances, subnetsOverlap } = require('../lib/InstanceConfig');
const { buildAtomicNftBatch, renderNftablesPolicy } = require('../lib/NftablesPolicy');
const { RuntimeApplier } = require('../lib/RuntimeApplier');

test('ресурсы AWG и WG независимы; вложенные mounts и пересечения запрещены', () => {
  const awg = instanceConfig();
  const wg = instanceConfig('wireguard');
  assert.equal(awg.interfaceName, 'awg0');
  assert.equal(wg.interfaceName, 'wg0');
  assert.equal(validateInstances([awg, wg]).length, 2);
  for (const field of ['interfaceName', 'containerName', 'tableName', 'listenPort', 'panelPort']) {
    assert.throws(() => validateInstances([awg, { ...wg, [field]: awg[field] }]), /Конфликт/);
  }
  assert.throws(() => validateInstances([awg, { ...wg, ipv4Subnet: '10.8.0.128/25' }]), /подсети/);
  assert.throws(() => validateInstances([{ ...awg, hostDataDirectory: '/srv/vpn' }, { ...wg, hostDataDirectory: '/srv/vpn/wg' }]), /каталоги/);
  assert(subnetsOverlap('fd00:1::/64', 'fd00:1::80/128', 6));
  assert.throws(() => validateInstances([{ ...awg, ipv6Subnet: 'fd00:1::/64' }, { ...wg, ipv6Subnet: 'fd00:1::/65' }]), /подсети/);
  assert.throws(() => instanceConfig('wireguard', { tableName: 'awg_easy_3' }), /другому/);
  assert.throws(() => instanceConfig('wireguard', { listenPort: 0 }), /порт/);
});

test('атомарный WG batch заменяет только WG table и отвергает чужие команды', () => {
  const policy = renderNftablesPolicy({ tableName: 'wg_easy_3', interfaceName: 'wg0', ipv4Subnet: '10.9.0.0/24', home4: ['10.9.0.2'], guest4: [] });
  assert.match(buildAtomicNftBatch(policy, { tableName: 'wg_easy_3', tableExists: true }), /^delete table inet wg_easy_3\n/);
  assert.doesNotMatch(policy, /awg_easy_3|"awg0"/);
  assert.throws(() => buildAtomicNftBatch(policy), /table/);
  assert.throws(() => buildAtomicNftBatch(policy + '\ntable inet чужая {}', { tableName: 'wg_easy_3' }), /чужие/);
  assert.throws(() => buildAtomicNftBatch(policy + '\nadd rule ip filter INPUT accept', { tableName: 'wg_easy_3' }), /чужие/);
});

test('остановка WG не удаляет таблицу и маркеры AWG', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'dual-isolation-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const calls = [];
  const runner = async (file, args) => {
    calls.push([file, ...args]);
    if (args[0] === '-a') return { stdout: 'chain DOCKER-USER {\n iifname "awg0" accept comment "awg_easy_3_forward_v4" # handle 10\n iifname "wg0" accept comment "wg_easy_3_forward_v4" # handle 11\n}' };
    return { stdout: '' };
  };
  const applier = new RuntimeApplier({ runtimeDirectory: directory, tableName: 'wg_easy_3', awgBinary: 'wg', awgQuickBinary: 'wg-quick', runner });
  await applier.down({ interfaceName: 'wg0' });
  assert(calls.some(c => c.join(' ') === 'nft delete table inet wg_easy_3'));
  assert(!calls.some(c => c.includes('awg_easy_3') || c.at(-1) === '10'));
  assert(calls.some(c => c.at(-1) === '11'));
  await assert.rejects(applier.removeDockerCompatibility(['awg_easy_3_forward_v4']), /другого/);
});

test('ошибка остановки активного интерфейса оставляет защитные правила', async () => {
  const calls = [];
  const applier = new RuntimeApplier({ runner: async (file, args) => {
    calls.push([file, ...args]);
    if (args[0] === 'down') throw Object.assign(new Error('Отказ'), { code: 1 });
    return { stdout: '' };
  } });
  await assert.rejects(applier.down(), /firewall сохранён/);
  assert(!calls.some(c => c[0] === 'nft'));
});

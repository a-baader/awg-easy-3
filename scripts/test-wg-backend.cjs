'use strict';

// Проверка выполняется только в одноразовых Docker namespaces и volume.
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const path = require('node:path');
const assert = require('node:assert/strict');
const image = process.argv[2] || 'vpn-easy-wg:dual-dev';
const prefix = 'vpn-wg-' + crypto.randomBytes(6).toString('hex');
const network = prefix + '-net', volume = prefix + '-data';
const containers = [];
const helper = path.join(__dirname, 'wg-backend-helper.cjs');
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 120000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const start = (name, mode) => {
  containers.push(name);
  docker('run', '-d', '--name', name, '--network', network, '--cap-add', 'NET_ADMIN', '--device', '/dev/net/tun',
    '--read-only', '--tmpfs', '/run:mode=0700', '--tmpfs', '/tmp:mode=1777',
    '-v', volume + ':/data' + (mode === 'client' ? ':ro' : ''), '-v', helper + ':/test.cjs:ro',
    '--entrypoint', 'node', image, '/test.cjs', mode);
};
async function ready(name, marker) {
  for (let i = 0; i < 50; i++) {
    if (docker('logs', name).includes(marker)) return;
    if (docker('inspect', '-f', '{{.State.Running}}', name) !== 'true') throw new Error('Тестовый контейнер завершился: ' + name);
    await wait(100);
  }
  throw new Error('Тестовый контейнер не готов: ' + name);
}
(async () => {
  docker('network', 'create', network); docker('volume', 'create', volume);
  const server = prefix + '-server', client = prefix + '-client';
  start(server, 'server'); await ready(server, 'Сервер готов.');
  start(client, 'client'); await ready(client, 'Клиент готов.');
  console.log(docker('exec', client, 'node', '/test.cjs', 'traffic'));
  console.log(docker('exec', server, 'node', '/test.cjs', 'check'));
  docker('exec', server, 'node', '/app/healthcheck.js');
  docker('stop', '-t', '10', server);
  assert.equal(docker('inspect', '-f', '{{.State.ExitCode}}', server), '0');
  console.log('PASS: официальный userspace WireGuard, импорт conf, handshake, реальный трафик к панели и healthcheck.');
})().catch(error => { console.error('Ошибка проверки WireGuard: ' + String(error.stderr || error.message).split('\n')[0]); process.exitCode = 1; }).finally(() => {
  for (const name of containers.reverse()) { try { docker('rm', '-f', name); } catch {} }
  try { docker('volume', 'rm', volume); } catch {}
  try { docker('network', 'rm', network); } catch {}
});

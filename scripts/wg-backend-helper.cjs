'use strict';
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const { Application } = require('/app/lib/Application');
const { UserspaceWireGuard } = require('/app/lib/UserspaceWireGuard');
const { runProcess } = require('/app/lib/ProcessRunner');
const mode = process.argv[2];
(async () => {
  if (mode === 'server') {
    const app = new Application({ backendName: 'wireguard', geoFactory: () => ({
      current: null, load: async () => null, start() {}, stop: async () => {} }) });
    const result = await app.initialize({ ipv6: null });
    await fs.writeFile('/data/client.conf', result.nativeConfig, { mode: 0o600 });
    await app.backend.validateEnvironment();
    await app.start();
    console.log('Сервер готов.');
    process.once('SIGTERM', () => app.stop().then(() => process.exit(0)).catch(() => process.exit(1)));
  } else if (mode === 'client') {
    const config = await fs.readFile('/data/client.conf', 'utf8');
    await fs.writeFile('/tmp/client.conf', config, { mode: 0o600 });
    const stripped = await runProcess('wg-quick', ['strip', '/tmp/client.conf']);
    await fs.writeFile('/tmp/client.stripped.conf', stripped.stdout, { mode: 0o600 });
    const driver = new UserspaceWireGuard();
    await driver.up({ interfaceName: 'wg-client', serverConfig: config, strippedConfigPath: '/tmp/client.stripped.conf' });
    await runProcess('ip', ['route', 'add', '10.9.0.0/24', 'dev', 'wg-client']);
    console.log('Клиент готов.');
    process.once('SIGTERM', () => driver.down('wg-client').then(() => process.exit(0)).catch(() => process.exit(1)));
  } else if (mode === 'traffic') {
    const response = await fetch('http://10.9.0.1:51825/api/v1/session', { signal: AbortSignal.timeout(10000) });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).authenticated, false);
    console.log('PASS: клиент получил ответ своей панели по туннелю.');
  } else if (mode === 'check') {
    const link = JSON.parse((await runProcess('ip', ['-j', '-d', 'link', 'show', 'dev', 'wg0'])).stdout)[0];
    assert.equal(link.linkinfo.info_kind, 'tun');
    const handshakes = (await runProcess('wg', ['show', 'wg0', 'latest-handshakes'])).stdout;
    assert(Number(handshakes.split(/\s+/)[1]) > 0);
    console.log('PASS: WG server использует TUN/userspace; handshake подтверждён.');
  }
})().catch(() => { console.error('Ошибка тестового помощника WireGuard: ' + mode); process.exitCode = 1; });

'use strict';

// Все интерфейсы и nftables живут в disposable namespace, общем для двух серверов.
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const path = require('node:path');
const assert = require('node:assert/strict');
const prefix = 'vpn-dual-' + crypto.randomBytes(6).toString('hex');
const network = prefix + '-net';
const images = { awg: process.argv[2] || 'vpn-easy-awg:dual-stage4', wg: process.argv[3] || 'vpn-easy-wg:dual-stage4' };
const helper = path.join(__dirname, 'dual-network-helper.cjs');
const containers = [], volumes = [];
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 120000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const execute = (name, ...args) => docker('exec', name, 'node', '/test.cjs', ...args);
const anchor = prefix + '-host';
function start(name, kind, mode, { shared = false, volume, args = [] } = {}) {
  containers.push(name);
  const settings = shared ? [] : ['--sysctl', 'net.ipv6.conf.all.disable_ipv6=0', '--sysctl', 'net.ipv4.ip_forward=1', '--sysctl', 'net.ipv6.conf.all.forwarding=1'];
  docker('run', '-d', '--name', name, '--network', shared ? 'container:' + anchor : network,
    '--cap-add', 'NET_ADMIN', '--device', '/dev/net/tun', ...settings,
    '--read-only', '--tmpfs', '/run:mode=0700', '--tmpfs', '/tmp:mode=1777',
    '-e', 'VPN_BACKEND=' + (kind === 'wg' ? 'wireguard' : 'amneziawg'),
    ...(volume ? ['-v', volume + ':/data' + (mode === 'client' ? ':ro' : '')] : []),
    '-v', helper + ':/test.cjs:ro', '--entrypoint', 'node', images[kind], '/test.cjs', mode, ...args);
}
async function ready(name, marker) {
  for (let i = 0; i < 60; i++) {
    const started = docker('inspect', '-f', '{{.State.StartedAt}}', name);
    if (docker('logs', '--since', started, name).includes(marker)) return;
    if (docker('inspect', '-f', '{{.State.Running}}', name) !== 'true') throw new Error('Контейнер завершился: ' + name + ' ' + docker('logs', name));
    await wait(100);
  }
  throw new Error('Контейнер не готов: ' + name + ' ' + docker('logs', name));
}
const check = (name, host, port, permission) => console.log(execute(name, 'traffic', host, String(port), permission));
async function reconnect(name, host, port) {
  for (let i = 0; i < 18; i++) {
    try { check(name, host, port, 'allow'); return; }
    catch (error) {
      if (!String(error.stderr).includes('Истёк срок ожидания')) throw error;
      console.log('Ожидание штатного переподключения клиента после restart…');
      await wait(500);
    }
  }
  throw new Error('Клиент не переподключился после restart за три минуты');
}
(async () => {
  const ula = 'fd66:' + crypto.randomBytes(2).toString('hex') + ':' + crypto.randomBytes(2).toString('hex') + '::/64';
  docker('network', 'create', '--ipv6', '--subnet', ula, network);
  start(anchor, 'awg', 'hold');
  const wan = prefix + '-wan'; start(wan, 'wg', 'wan'); await ready(wan, 'WAN готов.');
  const wanNet = JSON.parse(docker('inspect', wan))[0].NetworkSettings.Networks[network];
  const hostNet = JSON.parse(docker('inspect', anchor))[0].NetworkSettings.Networks[network];
  for (const n of [8, 9]) {
    docker('exec', wan, 'ip', 'route', 'add', `10.${n}.0.0/24`, 'via', hostNet.IPAddress);
    docker('exec', wan, 'ip', '-6', 'route', 'add', `fd42:${n}::/64`, 'via', hostNet.GlobalIPv6Address);
  }
  docker('exec', anchor, 'ip', 'route', 'add', '93.184.216.34/32', 'via', wanNet.IPAddress);
  docker('exec', anchor, 'ip', '-6', 'route', 'add', '2001:4860:dead::34/128', 'via', wanNet.GlobalIPv6Address);
  docker('exec', anchor, 'ip', 'address', 'add', '198.51.100.10/32', 'dev', 'lo');
  // Намеренно permissive INPUT для панелей: отрицательные тесты проверяют firewall приложения, а не отсутствие listener.
  const rules = ['add table ip filter', 'add chain ip filter DOCKER-USER', 'add chain ip filter FORWARD { type filter hook forward priority 0; policy drop; }', 'add rule ip filter FORWARD jump DOCKER-USER',
    'add table ip6 filter', 'add chain ip6 filter DOCKER-USER', 'add chain ip6 filter FORWARD { type filter hook forward priority 0; policy drop; }', 'add rule ip6 filter FORWARD jump DOCKER-USER'].join('\n');
  execFileSync('docker', ['exec', '-i', anchor, 'nft', '-f', '-'], { input: rules, stdio: ['pipe', 'pipe', 'pipe'] });
  const peers = {}, servers = {};
  for (const kind of ['awg', 'wg']) {
    const volume = prefix + '-' + kind + '-data'; volumes.push(volume); docker('volume', 'create', volume);
    const server = prefix + '-' + kind; servers[kind] = server;
    start(server, kind, 'server', { shared: true, volume }); await ready(server, 'Сервер готов.');
    peers[kind] = [];
    for (let i = 0; i < 3; i++) {
      const name = prefix + '-' + kind + '-peer-' + i;
      start(name, kind, 'client', { volume, args: [String(i)] }); await ready(name, 'Клиент готов.'); peers[kind].push(name);
    }
  }
  for (const [kind, n, panel] of [['awg', 8, 51821], ['wg', 9, 51825]]) {
    const [home, second, guest] = peers[kind];
    check(home, `10.${n}.0.1`, panel, 'allow'); check(home, `fd42:${n}::1`, panel, 'allow');
    for (const peer of [home, guest]) {
      check(peer, '93.184.216.34', 8080, 'allow'); check(peer, '2001:4860:dead::34', 8080, 'allow');
    }
    check(home, `10.${n}.0.3`, 8080, 'allow'); check(home, `fd42:${n}::3`, 8080, 'allow');
    check(guest, `10.${n}.0.1`, panel, 'block'); check(guest, `10.${n}.0.2`, 8080, 'block');
    const other = n === 8 ? 9 : 8, otherPanel = n === 8 ? 51825 : 51821;
    check(home, `10.${other}.0.1`, otherPanel, 'block'); check(home, `fd42:${other}::1`, otherPanel, 'block');
    check(home, `10.${other}.0.2`, 8080, 'block'); check(home, `fd42:${other}::2`, 8080, 'block');
    check(home, '198.51.100.10', otherPanel, 'block');
    check(wan, `10.${n}.0.1`, panel, 'block');
    check(wan, `fd42:${n}::1`, panel, 'block');
    for (const udpPort of [5353, 1900]) {
      const tag = kind + '-' + udpPort;
      execute(home, 'discover-send', tag, String(udpPort)); await wait(150);
      assert(Number(execute(second, 'discover-count', tag)) > 0, 'Нет Home discovery fan-out');
      assert.equal(Number(execute(guest, 'discover-count', tag)), 0);
      assert.equal(Number(execute(peers[kind === 'awg' ? 'wg' : 'awg'][0], 'discover-count', tag)), 0);
    }
    docker('exec', servers[kind], 'node', '/app/healthcheck.js');
  }
  for (const [stopped, active, n, panel] of [['awg', 'wg', 9, 51825], ['wg', 'awg', 8, 51821]]) {
    docker('stop', '-t', '10', servers[stopped]);
    assert.equal(docker('inspect', '-f', '{{.State.ExitCode}}', servers[stopped]), '0');
    check(peers[active][0], `10.${n}.0.1`, panel, 'allow'); check(peers[active][0], '93.184.216.34', 8080, 'allow');
    docker('start', servers[stopped]); await ready(servers[stopped], 'Сервер готов.');
    const restoredNetwork = stopped === 'awg' ? 8 : 9, restoredPanel = stopped === 'awg' ? 51821 : 51825;
    await reconnect(peers[stopped][0], `10.${restoredNetwork}.0.1`, restoredPanel);
  }
  console.log('PASS: два сервера, AWG/WG Home и Guest Internet, IPv4/IPv6, изоляция, discovery, независимая остановка и запуск.');
})().catch(error => { console.error(String(error.stderr || error.message).replace(/[A-Za-z0-9+/]{43}=/g, '[КЛЮЧ СКРЫТ]').slice(0, 3000)); process.exitCode = 1; }).finally(() => {
  for (const name of containers.reverse()) { try { docker('rm', '-f', name); } catch {} }
  for (const volume of volumes) { try { docker('volume', 'rm', volume); } catch {} }
  try { docker('network', 'rm', network); } catch {}
});

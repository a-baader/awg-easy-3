'use strict';

// Все интерфейсы и nftables живут в disposable namespace, общем для двух серверов.
const { execFileSync, spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const prefix = 'vpn-dual-' + crypto.randomBytes(6).toString('hex');
const network = prefix + '-net';
const images = { awg: process.argv[2] || 'vpn-easy-awg:dual-stage4', wg: process.argv[3] || 'vpn-easy-wg:dual-stage4' };
const guestCrossOnly = process.argv.includes('--guest-cross-only');
const helper = path.join(__dirname, 'dual-network-helper.cjs');
const containers = [], volumes = [];
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 120000, stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const execute = (name, ...args) => docker('exec', name, 'node', '/test.cjs', ...args);
const anchor = prefix + '-host';
const redact = value => String(value).replace(/[A-Za-z0-9+/]{43}=/g, '[КЛЮЧ СКРЫТ]');
function captureDiagnostics() {
  const directory = process.env.DUAL_DIAGNOSTIC_DIRECTORY;
  if (!directory) return;
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const records = {};
  for (const name of [...new Set(containers)]) {
    records[name] = {};
    for (const [label, command] of [
      ['state', ['inspect', '-f', '{{json .State}}', name]],
      ['processes', ['top', name, '-eo', 'pid,ppid,stat,comm']],
      ['logs', ['logs', '--timestamps', '--tail', '100', name]],
      ['addresses', ['exec', name, 'ip', '-j', 'address']],
      ['routes4', ['exec', name, 'ip', '-j', '-4', 'route', 'show', 'table', 'all']],
      ['routes6', ['exec', name, 'ip', '-j', '-6', 'route', 'show', 'table', 'all']],
      ['rules4', ['exec', name, 'ip', '-j', '-4', 'rule']],
    ]) {
      const result = spawnSync('docker', command, { encoding: 'utf8', timeout: 10_000 });
      records[name][label] = redact((result.stdout ?? '') + (result.stderr ?? '')
        + (result.error ? '\n' + result.error.message : ''));
    }
  }
  fs.writeFileSync(path.join(directory, prefix + '.json'), JSON.stringify(records, null, 2) + '\n', { mode: 0o600 });
  console.error('Сетевая диагностика сохранена до удаления контейнеров: ' + directory);
}
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
    const listeners = docker('exec', server, 'ss', '-H', '-lun');
    assert(!/0\.0\.0\.0:(?:1900|5353)\b|\*:(?:1900|5353)\b/.test(listeners), 'Обнаружен wildcard discovery listener');
    for (const port of [1900, 5353]) assert(listeners.includes(`10.${kind === 'awg' ? 8 : 9}.0.1:${port}`), 'Нет VPN discovery listener');
    peers[kind] = [];
    for (let i = 0; i < 3; i++) {
      const name = prefix + '-' + kind + '-peer-' + i;
      start(name, kind, 'client', { volume, args: [String(i)] }); await ready(name, 'Клиент готов.'); peers[kind].push(name);
    }
  }
  docker('exec', anchor, 'ip', '-6', 'address', 'add', '2001:db8:feed::10/128', 'dev', 'lo', 'nodad');
  docker('exec', wan, 'ip', 'route', 'add', '198.51.100.10/32', 'via', hostNet.IPAddress);
  docker('exec', wan, 'ip', '-6', 'route', 'add', '2001:db8:feed::10/128', 'via', hostNet.GlobalIPv6Address);
  docker('exec', '-d', anchor, 'node', '/test.cjs', 'host-echo'); await wait(300);
  check(wan, '198.51.100.10', 18080, 'allow'); check(wan, '2001:db8:feed::10', 18080, 'allow');
  // DNAT ведёт к реально работающему listener: проверяется фильтрация после NAT.
  const hairpin = 'add table ip test_hairpin\nadd chain ip test_hairpin prerouting { type nat hook prerouting priority dstnat; policy accept; }\nadd rule ip test_hairpin prerouting ip daddr 198.51.100.10 tcp dport 18081 dnat to 10.9.0.1:51825\nadd table ip6 test_hairpin6\nadd chain ip6 test_hairpin6 prerouting { type nat hook prerouting priority dstnat; policy accept; }\nadd rule ip6 test_hairpin6 prerouting ip6 daddr 2001:db8:feed::10 tcp dport 18081 dnat to [fd42:9::1]:51825\n';
  execFileSync('docker', ['exec', '-i', anchor, 'nft', '-f', '-'], { input: hairpin });
  for (const [kind, n, panel] of [['awg', 8, 51821], ['wg', 9, 51825]]) {
    const [home, second, guest] = peers[kind];
    check(home, `10.${n}.0.1`, panel, 'allow'); check(home, `fd42:${n}::1`, panel, 'allow');
    for (const peer of [home, guest]) {
      check(peer, '93.184.216.34', 8080, 'allow'); check(peer, '2001:4860:dead::34', 8080, 'allow');
    }
    check(home, `10.${n}.0.3`, 8080, 'allow'); check(home, `fd42:${n}::3`, 8080, 'allow');
    check(second, `10.${n}.0.2`, 8080, 'allow'); check(second, `fd42:${n}::2`, 8080, 'allow');
    check(guest, `10.${n}.0.1`, panel, 'block'); check(guest, `10.${n}.0.2`, 8080, 'block');
    check(guest, `fd42:${n}::1`, panel, 'block'); check(guest, `fd42:${n}::2`, 8080, 'block');
    const other = n === 8 ? 9 : 8, otherPanel = n === 8 ? 51825 : 51821;
    check(home, `10.${other}.0.1`, otherPanel, 'block'); check(home, `fd42:${other}::1`, otherPanel, 'block');
    check(home, `10.${other}.0.2`, 8080, 'block'); check(home, `fd42:${other}::2`, 8080, 'block');
    for (const peer of [second, guest]) {
      check(peer, `10.${other}.0.1`, otherPanel, 'block'); check(peer, `fd42:${other}::1`, otherPanel, 'block');
      check(peer, `10.${other}.0.2`, 8080, 'block'); check(peer, `fd42:${other}::2`, 8080, 'block');
    }
    check(home, '198.51.100.10', otherPanel, 'block');
    for (const peer of [home, guest]) {
      docker('exec', peer, 'ip', 'route', 'add', hostNet.IPAddress + '/32', 'dev', 'peer0');
      check(peer, '198.51.100.10', 18080, 'block'); check(peer, '2001:db8:feed::10', 18080, 'block');
      check(peer, hostNet.IPAddress, 18080, 'block'); check(peer, '198.51.100.10', 18081, 'block');
      check(peer, '2001:db8:feed::10', 18081, 'block');
    }
    check(wan, `10.${n}.0.1`, panel, 'block');
    check(wan, `fd42:${n}::1`, panel, 'block');
    check(wan, '198.51.100.10', 18081, 'block'); check(wan, '2001:db8:feed::10', 18081, 'block');
    for (const udpPort of [5353, 1900]) for (const multicast of [true, false]) {
      const destination = multicast ? (udpPort === 5353 ? '224.0.0.251' : '239.255.255.250') : `10.${n}.0.1`;
      const tag = kind + '-' + udpPort + '-' + (multicast ? 'multicast' : 'unicast');
      execute(home, 'discover-send', tag, String(udpPort), `10.${n}.0.2`, destination); await wait(150);
      assert(Number(execute(second, 'discover-count', tag)) > 0, 'Нет Home discovery fan-out');
      assert.equal(Number(execute(guest, 'discover-count', tag)), 0);
      assert.equal(Number(execute(peers[kind === 'awg' ? 'wg' : 'awg'][0], 'discover-count', tag)), 0);
      assert.equal(Number(execute(wan, 'discover-count', tag)), 0);
      const guestTag = tag + '-guest';
      execute(guest, 'discover-send', guestTag, String(udpPort), `10.${n}.0.4`, destination); await wait(150);
      for (const receiver of [home, second, wan, peers[kind === 'awg' ? 'wg' : 'awg'][0]]) assert.equal(Number(execute(receiver, 'discover-count', guestTag)), 0);
      for (const [sender, source, target, label] of [
        [home, `10.${n}.0.2`, `10.${other}.0.1`, 'foreign-vpn'],
        [wan, wanNet.IPAddress, `10.${n}.0.1`, 'wan'],
      ]) {
        const blockedTag = tag + '-' + label;
        execute(sender, 'discover-send', blockedTag, String(udpPort), source, target); await wait(150);
        for (const receiver of [...peers.awg, ...peers.wg, wan]) assert.equal(Number(execute(receiver, 'discover-count', blockedTag)), 0);
      }
    }
    docker('exec', servers[kind], 'node', '/app/healthcheck.js');
    if (guestCrossOnly) continue;
    const policy = changes => execute(servers[kind], 'policy', 'home-admin', JSON.stringify(changes));
    policy({ ipv6Enabled: false });
    check(home, '2001:4860:dead::34', 8080, 'block'); check(home, '93.184.216.34', 8080, 'allow');
    policy({ ipv6Enabled: true, ipv4Enabled: false });
    check(home, '93.184.216.34', 8080, 'block'); check(home, '2001:4860:dead::34', 8080, 'allow');
    policy({ ipv4Enabled: true });
    for (const [mode, country, allow] of [['allow', 'RU', true], ['block', 'RU', false], ['allow', 'ZZ', false]]) {
      policy({ geoPolicy: { mode, countries: [country] } });
      for (const host of ['93.184.216.34', '2001:4860:dead::34']) check(home, host, 8080, allow ? 'allow' : 'block');
      check(home, `10.${n}.0.1`, panel, 'allow');
    }
    policy({ geoPolicy: { mode: 'off', countries: [] } });
    policy({ geoPolicy: { mode: 'allow', countries: ['RU'] } });
    docker('exec', servers[kind], 'node', '-e', "require('node:fs').writeFileSync('/data/geo-unavailable','1',{mode:0o600})");
    docker('restart', servers[kind]);
    for (let attempt = 0; attempt < 50 && docker('inspect', '-f', '{{.State.Running}}', servers[kind]) === 'true'; attempt++) await wait(100);
    assert.equal(docker('inspect', '-f', '{{.State.Running}}', servers[kind]), 'false', 'Без базы GeoIP экземпляр не остановился');
    check(home, '93.184.216.34', 8080, 'block'); check(home, '2001:4860:dead::34', 8080, 'block');
    check(peers[kind === 'awg' ? 'wg' : 'awg'][0], `10.${other}.0.1`, otherPanel, 'allow');
    docker('run', '--rm', '--network', 'none', '-v', prefix + '-' + kind + '-data:/data', '--entrypoint', 'node', images[kind], '-e', "require('node:fs').unlinkSync('/data/geo-unavailable')");
    docker('start', servers[kind]); await ready(servers[kind], 'Сервер готов.');
    await reconnect(home, `10.${n}.0.1`, panel);
    policy({ geoPolicy: { mode: 'off', countries: [] } });
    await reconnect(guest, '93.184.216.34', 8080);
    docker('exec', '-d', guest, 'node', '/test.cjs', 'flow');
    for (let attempt = 0; attempt < 30; attempt++) {
      await wait(100);
      try { if (Number(execute(guest, 'flow-count')) > 0) break; } catch {}
    }
    assert(Number(execute(guest, 'flow-count')) > 0, 'Нет исходного потока для проверки отзыва');
    execute(servers[kind], 'policy', 'Guest', JSON.stringify({ enabled: false }));
    await wait(1500); const stoppedCount = Number(execute(guest, 'flow-count')); await wait(1500);
    assert.equal(Number(execute(guest, 'flow-count')), stoppedCount, 'После отзыва peer продолжилась передача по открытому TCP');
    check(guest, '93.184.216.34', 8080, 'block'); check(guest, '2001:4860:dead::34', 8080, 'block');
    execute(servers[kind], 'policy', 'Guest', JSON.stringify({ enabled: true }));
    await reconnect(guest, '93.184.216.34', 8080);
  }
  for (const [stopped, active, n, panel] of guestCrossOnly ? [] : [['awg', 'wg', 9, 51825], ['wg', 'awg', 8, 51821]]) {
    docker('stop', '-t', '10', servers[stopped]);
    assert.equal(docker('inspect', '-f', '{{.State.ExitCode}}', servers[stopped]), '0');
    check(peers[active][0], `10.${n}.0.1`, panel, 'allow'); check(peers[active][0], '93.184.216.34', 8080, 'allow');
    docker('start', servers[stopped]); await ready(servers[stopped], 'Сервер готов.');
    const restoredNetwork = stopped === 'awg' ? 8 : 9, restoredPanel = stopped === 'awg' ? 51821 : 51825;
    await reconnect(peers[stopped][0], `10.${restoredNetwork}.0.1`, restoredPanel);
  }
  console.log(guestCrossOnly ? 'PASS: Home A/B и Guest, меж-VPN запреты IPv4/IPv6 и WAN DNAT.' : 'PASS: AWG/WG Home и Guest, IPv4/IPv6, DNAT, host/Docker, GeoIP fail-closed, отзыв TCP, discovery и независимые restart.');
})().catch(error => {
  console.error(redact(error.stderr || error.message).slice(0, 3000));
  try { captureDiagnostics(); } catch (diagnosticError) { console.error('Не удалось сохранить сетевую диагностику: ' + diagnosticError.message); }
  process.exitCode = 1;
}).finally(() => {
  for (const name of containers.reverse()) { try { docker('rm', '-f', name); } catch {} }
  for (const volume of volumes) { try { docker('volume', 'rm', volume); } catch {} }
  try { docker('network', 'rm', network); } catch {}
});

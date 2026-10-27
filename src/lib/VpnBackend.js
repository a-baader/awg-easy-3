'use strict';

const { AwgKeyManager } = require('./AwgKeyManager');
const { instanceConfig } = require('./InstanceConfig');
const { runProcess } = require('./ProcessRunner');
const { renderServerConfig, renderClientConfig } = require('./AwgConfigRenderer');
const { UserspaceWireGuard } = require('./UserspaceWireGuard');

const backendKind = server => server?.backend ?? 'amneziawg';

class VpnBackend {
  constructor(kind = 'amneziawg', { runner = runProcess, spawnProcess, wait } = {}) {
    this.instance = instanceConfig(kind);
    this.kind = kind;
    this.binary = kind === 'wireguard' ? 'wg' : 'awg';
    this.quickBinary = kind === 'wireguard' ? 'wg-quick' : 'awg-quick';
    this.engine = kind === 'wireguard' ? 'wireguard-go' : 'amneziawg-go';
    this.runner = runner;
    this.keyManager = new AwgKeyManager({ binary: this.binary, runner });
    this.driver = kind === 'wireguard' ? new UserspaceWireGuard({ runner, spawnProcess, wait }) : undefined;
  }

  capabilities() {
    return Object.freeze({ backend: this.kind, nativeConfig: true, nativeQr: true,
      vpnUrl: this.kind === 'amneziawg', obfuscation: this.kind === 'amneziawg',
      ipv4: true, ipv6: true, geoip: true, discovery: true });
  }

  assertState(state) {
    if (backendKind(state.server) !== this.kind) throw new Error('Backend образа не соответствует сохранённому состоянию');
  }

  async validateEnvironment() {
    const version = await this.runner(this.binary, ['--version']);
    const label = this.kind === 'wireguard' ? 'wireguard-tools' : 'amneziawg-tools';
    if (!version.stdout.startsWith(label + ' v')) throw new Error('Установлены несовместимые VPN tools');
    const engine = await this.runner(this.engine, ['--version']);
    if (!engine.stdout.startsWith(this.engine + ' ')) throw new Error('Установлен несовместимый VPN engine');
    if (this.kind === 'amneziawg') {
      let help;
      try { help = (await this.runner(this.binary, ['set', '--help'])).stdout; }
      catch (error) { help = error.stderr ?? ''; }
      if (!['header-protection-key', 'content-padding-addition', 'random-trailers', 'disable-cookies'].every(field => help.includes(field))) {
        throw new Error('AWG tools не поддерживают обязательные возможности AmneziaWG 3.1');
      }
    }
    return this.capabilities();
  }

  renderServerConfig(options) { return renderServerConfig({ ...options, protocol: this.kind }); }
  renderClientConfig(options) { return renderClientConfig({ ...options, protocol: this.kind }); }
  getStatus(interfaceName) { return this.runner(this.binary, ['show', interfaceName, 'dump'], { timeoutMs: 5000 }); }
  getLatestHandshakes(interfaceName) { return this.runner(this.binary, ['show', interfaceName, 'latest-handshakes']); }
  getTransferCounters(interfaceName) { return this.runner(this.binary, ['show', interfaceName, 'transfer']); }
  runtimeOptions() {
    return { tableName: this.instance.tableName, awgBinary: this.binary,
      awgQuickBinary: this.quickBinary, interfaceDriver: this.driver };
  }
}

const createBackend = (kind = 'amneziawg', options) => new VpnBackend(kind, options);
module.exports = { VpnBackend, backendKind, createBackend };

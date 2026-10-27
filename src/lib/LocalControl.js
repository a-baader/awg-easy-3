'use strict';

const net = require('node:net');
const fs = require('node:fs/promises');
const path = require('node:path');
const { publicClient, defaultQrGenerator } = require('./ApiService');

// Только Unix socket 0600 в runtime 0700; секреты принимаются через JSON/stdin.
class LocalControl {
  constructor({ socketPath, store, clientManager, passwordManager, backend, diagnostics, setMaintenance }) {
    Object.assign(this, { socketPath, store, clientManager, passwordManager, backend, diagnostics, setMaintenance });
    this.server = null;
  }

  async selector(query) {
    if (typeof query !== 'string' || !query.trim()) throw new Error('Укажите имя или ID клиента');
    const state = await this.store.load();
    const client = state.clients.find(c => c.id === query || c.name.trim().toLocaleLowerCase() === query.trim().toLocaleLowerCase());
    if (!client) throw Object.assign(new Error('Клиент не найден'), { code: 'CLIENT_NOT_FOUND' });
    return client;
  }

  async execute({ operation, args = {} }) {
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Некорректные аргументы');
    if (operation === 'maintenance.begin' || operation === 'maintenance.end') {
      await this.setMaintenance(operation === 'maintenance.begin');
      return { maintenance: operation === 'maintenance.begin' };
    }
    if (operation === 'client.list') return (await this.store.load()).clients.map(publicClient);
    if (operation === 'client.add') return publicClient((await this.clientManager.createClient({ name: args.name, networkGroup: args.group })).client);
    if (['client.disable', 'client.enable', 'client.remove', 'client.export'].includes(operation)) {
      const client = await this.selector(args.name);
      if (operation === 'client.remove') { await this.clientManager.deleteClient(client.id); return { removed: true }; }
      if (operation !== 'client.export') return publicClient((await this.clientManager.updateClient(client.id, { enabled: operation === 'client.enable' })).client);
      const artifact = await this.clientManager.getClientExport(client.id);
      if (args.format === 'conf') return { value: artifact.nativeConfig };
      if (args.format === 'qr') return { value: await defaultQrGenerator(artifact.nativeConfig) };
      if (args.format === 'vpn-url' && this.backend.capabilities().vpnUrl) return { value: artifact.vpnLink };
      throw Object.assign(new Error('Неподдерживаемый формат экспорта этого backend'), { code: 'UNSUPPORTED_FORMAT' });
    }
    if (operation === 'reset-password') {
      if (args.password !== undefined && typeof args.password !== 'string') throw new Error('Некорректный пароль');
      return { password: await this.passwordManager.resetPassword(args.password) };
    }
    if (operation === 'settings') {
      const { server } = await this.store.load();
      return { backend: this.backend.kind, interface: server.interfaceName, address4: server.address4,
        network4: server.ipv4Subnet, address6: server.address6, network6: server.ipv6Subnet,
        udp_port: server.listenPort, panel_port: server.panelPort, endpoint: server.endpointHost,
        panel_url: `http://${server.address4}:${server.panelPort}/`, language: server.uiLanguage };
    }
    if (operation === 'diagnose') {
      const state = await this.store.load();
      let engine = false, panel = false;
      try { await this.backend.validateEnvironment(); await this.backend.getLatestHandshakes(state.server.interfaceName); engine = true; } catch {}
      try { panel = (await fetch(`http://${state.server.address4}:${state.server.panelPort}/api/v1/session`, { signal: AbortSignal.timeout(3000) })).ok; } catch {}
      return { backend: this.backend.kind, engine, panel, clients: await this.diagnostics.snapshot().catch(() => null),
        packet_probes: { status: 'requires_test_peer', note: 'Handshake и локальный HTTP не доказывают доставку peer-трафика' } };
    }
    throw new Error('Неизвестная операция управления');
  }

  async start() {
    await fs.mkdir(path.dirname(this.socketPath), { recursive: true, mode: 0o700 });
    // Остаток socket не удаляется автоматически: это может быть второй живой процесс.
    this.server = net.createServer(socket => {
      socket.setEncoding('utf8');
      socket.setTimeout(30000, () => socket.destroy());
      let input = '', handled = false;
      socket.on('error', () => {});
      socket.on('data', chunk => {
        if (handled) return;
        input += chunk.toString('utf8');
        if (Buffer.byteLength(input) > 16384) { handled = true; socket.end(JSON.stringify({ ok: false, error: 'Слишком большой запрос управления' }) + '\n'); return; }
        const end = input.indexOf('\n');
        if (end < 0) return;
        handled = true;
        Promise.resolve().then(() => this.execute(JSON.parse(input.slice(0, end))))
          .then(value => socket.end(JSON.stringify({ ok: true, value }) + '\n'))
          .catch(error => {
            const code = ['LAST_HOME', 'CURRENT_PANEL_PATH', 'IPV6_UNAVAILABLE', 'GEO_UNAVAILABLE', 'CLIENT_NAME_EXISTS', 'CLIENT_NOT_FOUND', 'UNSUPPORTED_FORMAT', 'MAINTENANCE'].includes(error.code) ? error.code : 'OPERATION_FAILED';
            // В ответ не включаются исключения движка, JSON состояния или секреты.
            socket.end(JSON.stringify({ ok: false, code, error: 'Не удалось выполнить операцию управления: ' + code }) + '\n');
          });
      });
    });
    await new Promise((resolve, reject) => { this.server.once('error', reject); this.server.listen(this.socketPath, resolve); });
    await fs.chmod(this.socketPath, 0o600);
  }

  async close() {
    if (!this.server) return;
    await new Promise((resolve, reject) => this.server.close(error => error ? reject(error) : resolve()));
    this.server = null;
    await fs.rm(this.socketPath, { force: true });
  }
}
module.exports = { LocalControl };

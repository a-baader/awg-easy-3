'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');

const { ApiService } = require('./ApiService');
const { buildAwgArtifacts } = require('./AwgArtifacts');
const { BootstrapInstaller } = require('./BootstrapInstaller');
const { ClientManager } = require('./ClientManager');
const { ClientDiagnostics } = require('./ClientDiagnostics');
const { DiscoveryRelay } = require('./DiscoveryRelay');
const { GeoIpUpdater } = require('./GeoIpUpdater');
const { HttpServer } = require('./HttpServer');
const { PasswordManager } = require('./PasswordManager');
const { runProcess } = require('./ProcessRunner');
const { RuntimeApplier } = require('./RuntimeApplier');
const { SessionManager } = require('./SessionManager');
const { StateStore } = require('./StateStore');
const { createBackend } = require('./VpnBackend');
const { NetworkDetector } = require('./NetworkDetector');
const { LocalControl } = require('./LocalControl');

class Application {
  constructor({
    dataDirectory = '/data',
    runtimeDirectory,
    backendName = 'amneziawg',
    backend,
    dualMode = false,
    controlEnabled = false,
    maintenance = false,
    publicDirectory = path.join(__dirname, '..', 'www'),
    runner = runProcess,
    fileSystem = fs,
    discoveryFactory = (options) => new DiscoveryRelay(options),
    httpFactory = (options) => new HttpServer(options),
    geoFactory = (options) => new GeoIpUpdater(options),
  } = {}) {
    this.backend = backend ?? createBackend(backendName, { runner });
    this.dualMode = dualMode;
    this.controlEnabled = controlEnabled;
    this.maintenance = maintenance;
    this.control = null;
    this.artifactBuilder = options => buildAwgArtifacts({ ...options, isolation: this.dualMode });
    this.dataDirectory = path.resolve(dataDirectory);
    this.runtimeDirectory = path.resolve(runtimeDirectory ?? this.backend.instance.runtimeDirectory);
    this.publicDirectory = path.resolve(publicDirectory);
    this.runner = runner;
    this.fs = fileSystem;
    if (typeof discoveryFactory !== 'function' || typeof httpFactory !== 'function') {
      throw new TypeError('discoveryFactory and httpFactory must be functions');
    }
    this.discoveryFactory = discoveryFactory;
    this.httpFactory = httpFactory;
    this.geo = geoFactory({ directory: path.join(this.dataDirectory, 'geoip'),
      onError: error => console.warn(`GeoIP update: ${error.message}`) });
    this.store = new StateStore(path.join(this.dataDirectory, 'state.json'));
    this.applier = new RuntimeApplier({ runtimeDirectory: this.runtimeDirectory, runner, ...this.backend.runtimeOptions(),
      protectBeforeInterface: dualMode || this.backend.kind === 'wireguard' });
    this.http = null;
    this.http6 = null;
    this.discovery = null;
    this.state = null;
  }

  async initialize({
    endpointHost,
    wanInterface,
    firstClientName = 'Home admin',
    listenPort = this.backend.instance.listenPort,
    panelPort = this.backend.instance.panelPort,
    uiLanguage = 'en',
    interfaceName,
    ipv4Subnet,
    serverAddress4,
    firstClientAddress4,
    ipv6,
  } = {}) {
    const result = await new BootstrapInstaller({ store: this.store, backend: this.backend,
      networkDetector: new NetworkDetector({ runner: this.runner }) }).install({
      endpointHost,
      wanInterface,
      firstClientName,
      listenPort,
      panelPort,
      uiLanguage,
      interfaceName, ipv4Subnet, serverAddress4, firstClientAddress4, ipv6,
    });
    const artifacts = this.artifactBuilder({
      server: result.state.server,
      clients: result.state.clients,
    });
    return Object.freeze({
      bootstrapPassword: result.bootstrapPassword,
      panelUrl: `http://${result.state.server.address4}:${result.state.server.panelPort}`,
      vpnLink: artifacts.clientArtifacts['home-admin'].vpnLink,
      nativeConfig: artifacts.clientArtifacts['home-admin'].nativeConfig,
    });
  }

  async settings() {
    const state = await this.store.load();
    if (!state) throw new Error('wg-dual-easy is not initialized; run the init command first');
    // An explicit allowlist: never return keys, password hashes or client data.
    return Object.freeze({
      AWG_HOST: state.server.endpointHost,
      AWG_PORT: state.server.listenPort,
      AWG_PANEL_PORT: state.server.panelPort,
      AWG_LANG: state.server.uiLanguage ?? 'en',
    });
  }

  async interfaceActive(interfaceName) {
    try {
      await this.runner(this.backend.binary, ['show', interfaceName]);
      return true;
    } catch (error) {
      if (error && (error.code === 1 || error.exitCode === 1)) return false;
      throw error;
    }
  }

  async start() {
    const state = await this.store.load();
    if (!state) throw new Error('wg-dual-easy is not initialized; run the init command first');
    this.backend.assertState(state);
    // A reused Application must not call a previous runtime's installer before
    // its new interface exists. Startup may persist data, but cannot apply rules yet.
    this.geo.install = async (snapshot, commit) => commit();
    await this.geo.load();
    if (!this.geo.current && state.clients.some(client => client.geoPolicy && client.geoPolicy.mode !== 'off')) {
      await this.geo.refresh();
    }
    const artifacts = this.artifactBuilder({ server: state.server, clients: state.clients,
      geoDatabase: this.geo.current?.database ?? {} });
    await this.applier.apply({
      serverConfig: artifacts.serverConfig,
      nftables: artifacts.nftables,
      interfaceName: state.server.interfaceName,
      interfaceActive: await this.interfaceActive(state.server.interfaceName),
    });
    this.state = state;
    try {
      const passwordManager = new PasswordManager({ store: this.store, isReadOnly: () => this.maintenance });
      const sessionManager = new SessionManager({ store: this.store });
      this.discovery = this.discoveryFactory({ isolateUnicast: this.dualMode });
      await this.discovery.start(state);
      const clientManager = new ClientManager({
        store: this.store,
        applier: this.applier,
        keyManager: this.backend.keyManager,
        artifactBuilder: this.artifactBuilder,
        isReadOnly: () => this.maintenance,
        geoDatabase: () => this.geo.current?.database ?? {},
        onStateChanged: (nextState) => this.discovery.refresh(nextState),
      });
      this.clientManager = clientManager;
      this.geo.install = (snapshot, commit) => clientManager.installGeoSnapshot(snapshot, commit);
      const diagnostics = new ClientDiagnostics({ store: this.store, runner: this.runner, binary: this.backend.binary });
      const api = new ApiService({ store: this.store, passwordManager, sessionManager, clientManager, diagnostics,
        geoStatus: () => ({ ...this.geo.status, countries: this.geo.current?.countries ?? [] }) });
      this.http = this.httpFactory({ api, publicDirectory: this.publicDirectory });
      const listening = await this.http.listen({ host: state.server.address4, port: state.server.panelPort });
      if (state.server.address6 && state.server.ipv6Subnet) {
        this.http6 = this.httpFactory({ api, publicDirectory: this.publicDirectory });
        await this.http6.listen({ host: state.server.address6, port: state.server.panelPort });
      }
      if (this.controlEnabled) {
        this.control = new LocalControl({ socketPath: path.join(this.runtimeDirectory, 'control.sock'),
          store: this.store, clientManager, passwordManager, backend: this.backend, diagnostics,
          setMaintenance: value => this.store.serialize(() => { this.maintenance = value; }) });
        await this.control.start();
      }
      this.geo.start();
      return listening;
    } catch (error) {
      try {
        await this.stop();
      } catch (rollbackError) {
        error.rollbackErrors = Object.freeze(
          rollbackError instanceof AggregateError ? [...rollbackError.errors] : [rollbackError],
        );
      }
      throw error;
    }
  }

  async stop() {
    const errors = [];
    if (this.control) await this.control.close().catch(error => errors.push(error));
    this.control = null;
    await this.geo.stop().catch(error => errors.push(error));
    if (this.http) await this.http.close().catch((error) => errors.push(error));
    if (this.http6) await this.http6.close().catch((error) => errors.push(error));
    if (this.discovery) await this.discovery.stop().catch((error) => errors.push(error));
    if (this.state) {
      await this.applier.down({ interfaceName: this.state.server.interfaceName }).catch((error) => errors.push(error));
    }
    this.http = null;
    this.http6 = null;
    this.discovery = null;
    this.state = null;
    if (errors.length > 0) throw new AggregateError(errors, 'Application shutdown failed');
  }

  async resetPassword(password) {
    return new PasswordManager({ store: this.store, isReadOnly: () => this.maintenance }).resetPassword(password);
  }

  async exportClient(selector) {
    const query = typeof selector === 'string' ? selector.trim() : '';
    if (!query) throw new Error('Client name or ID is required');
    const state = await this.store.load();
    if (!state) throw new Error('wg-dual-easy is not initialized');
    const folded = query.toLocaleLowerCase();
    const client = state.clients.find((item) => item.id === query
      || item.name.trim().toLocaleLowerCase() === folded);
    if (!client) throw new Error(`Unknown client: ${query}`);
    // Exporting keys/routes does not need a country database or firewall build.
    const artifacts = this.artifactBuilder({ server: state.server,
      clients: state.clients.map(client => ({ ...client, geoPolicy: undefined })) });
    const artifact = artifacts.clientArtifacts[client.id];
    return Object.freeze({ clientName: client.name, vpnLink: artifact.vpnLink,
      ...(this.backend.kind === 'wireguard' ? { nativeConfig: artifact.nativeConfig } : {}) });
  }

}

module.exports = { Application };

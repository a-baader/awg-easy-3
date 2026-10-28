'use strict';

const { Application } = require('./lib/Application');
const { safeMessage } = require('./lib/SafeLog');

const command = process.argv[2] ?? 'serve';
const application = new Application({
  backendName: process.env.VPN_BACKEND ?? 'amneziawg',
  dualMode: process.env.VPN_DUAL_MODE === '1',
  controlEnabled: true,
  maintenance: process.env.VPN_MAINTENANCE === '1',
  dataDirectory: process.env.VPN_DATA_DIR ?? process.env.AWG_EASY_DATA_DIR ?? '/data',
  runtimeDirectory: process.env.VPN_RUNTIME_DIR ?? process.env.AWG_EASY_RUNTIME_DIR,
});

const printBootstrap = async (result) => {
  if (application.backend.kind === 'wireguard') {
    process.stdout.write(`WireGuard настроен.\nПанель после подключения VPN: ${result.panelUrl}\nПароль панели (показан один раз): ${result.bootstrapPassword}\n\nПервый Home-профиль WireGuard:\n${result.nativeConfig}\n`);
    return;
  }
  process.stdout.write([
    '',
    'wg-dual-easy initialized successfully.',
    `Panel after VPN connection: ${result.panelUrl}`,
    `Panel password (shown once): ${result.bootstrapPassword}`,
    '',
    'First Home profile for AmneziaVPN:',
    result.vpnLink,
    '',
  ].join('\n'));
};

const main = async () => {
  if (command === 'init-json') {
    process.stdin.setEncoding('utf8');
    let input = '';
    for await (const chunk of process.stdin) {
      input += chunk;
      if (Buffer.byteLength(input) > 16384) throw new Error('Слишком большой запрос инициализации');
    }
    const options = JSON.parse(input);
    const allowed = new Set(['endpointHost', 'wanInterface', 'firstClientName', 'listenPort', 'panelPort', 'uiLanguage', 'interfaceName', 'ipv4Subnet', 'serverAddress4', 'firstClientAddress4', 'ipv6']);
    if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).some(key => !allowed.has(key))) throw new Error('Некорректные параметры инициализации');
    await application.backend.validateEnvironment();
    process.stdout.write(JSON.stringify(await application.initialize(options)) + '\n');
    return;
  }
  if (command === 'init') {
    await application.backend.validateEnvironment();
    return printBootstrap(await application.initialize({
      endpointHost: process.env.VPN_HOST || process.env.AWG_HOST || undefined,
      wanInterface: process.env.AWG_WAN_INTERFACE || undefined,
      firstClientName: process.env.AWG_FIRST_CLIENT_NAME || 'Home admin',
      listenPort: Number(process.env.VPN_PORT ?? process.env.AWG_PORT ?? application.backend.instance.listenPort),
      panelPort: Number(process.env.VPN_PANEL_PORT ?? process.env.AWG_PANEL_PORT ?? application.backend.instance.panelPort),
      uiLanguage: process.env.VPN_LANG || process.env.AWG_LANG || 'en',
    }));
  }
  if (command === 'settings') {
    const settings = await application.settings();
    process.stdout.write(Object.entries(settings).map(([key, value]) => `${key}=${value}\n`).join(''));
    return;
  }
  if (command === 'reset-password') {
    const password = await application.resetPassword(process.argv[3]);
    process.stdout.write(`New panel password (shown once): ${password}\n`);
    return;
  }
  if (command === 'export-client') {
    const exported = await application.exportClient(process.argv[3]);
    if (exported.nativeConfig) { process.stdout.write(exported.nativeConfig); return; }
    process.stdout.write(`AmneziaVPN profile for ${exported.clientName}:\n${exported.vpnLink}\n`);
    return;
  }
  if (command !== 'serve') throw new Error(`Unknown command: ${command}`);

  await application.backend.validateEnvironment();
  const address = await application.start();
  process.stdout.write(`wg-dual-easy panel listening on http://${address.address}:${address.port}\n`);
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await application.stop();
  };
  process.once('SIGTERM', () => stop().then(() => process.exit(0)).catch((error) => {
    console.error('[' + application.backend.kind + '] ' + safeMessage(error)); process.exit(1);
  }));
  process.once('SIGINT', () => stop().then(() => process.exit(0)).catch((error) => {
    console.error('[' + application.backend.kind + '] ' + safeMessage(error)); process.exit(1);
  }));
};

main().catch((error) => {
  console.error('[' + application.backend.kind + '] ' + safeMessage(error));
  process.exitCode = 1;
});

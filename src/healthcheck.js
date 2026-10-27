'use strict';

const path = require('node:path');
const { StateStore } = require('./lib/StateStore');
const { createBackend } = require('./lib/VpnBackend');

(async () => {
  const backend = createBackend(process.env.VPN_BACKEND ?? 'amneziawg');
  const directory = process.env.VPN_DATA_DIR ?? process.env.AWG_EASY_DATA_DIR ?? '/data';
  const state = await new StateStore(path.join(directory, 'state.json')).load();
  if (!state) throw new Error('VPN ещё не настроен');
  backend.assertState(state);
  await backend.runner(backend.binary, ['show', state.server.interfaceName], { timeoutMs: 3000 });
  const response = await fetch(`http://${state.server.address4}:${state.server.panelPort}/api/v1/session`, { signal: AbortSignal.timeout(3000) });
  if (!response.ok) throw new Error('Панель недоступна');
})().catch(() => { console.error('Проверка состояния VPN не пройдена'); process.exitCode = 1; });

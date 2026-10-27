'use strict';

const { spawn } = require('node:child_process');
const net = require('node:net');
const { runProcess } = require('./ProcessRunner');

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

// Официальный engine запускается напрямую: наличие kernel-модуля не меняет backend.
class UserspaceWireGuard {
  constructor({ runner = runProcess, spawnProcess = spawn, wait = pause } = {}) {
    this.runner = runner;
    this.spawnProcess = spawnProcess;
    this.wait = wait;
    this.child = null;
    this.interfaceName = null;
  }

  async up({ interfaceName, serverConfig, strippedConfigPath }) {
    if (this.child) throw new Error('Userspace WireGuard уже запущен');
    if (!/^[a-zA-Z0-9_.-]{1,15}$/.test(interfaceName)) throw new TypeError('Некорректное имя WireGuard-интерфейса');
    const section = serverConfig.match(/^\[Interface\]\s*\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m)?.[1];
    const addressLines = section?.match(/^Address\s*=\s*(.+)$/gm) ?? [];
    if (addressLines.length !== 1) throw new Error('Не найдены адреса WireGuard-интерфейса');
    const addresses = addressLines[0].split('=')[1].split(',').map(value => value.trim());
    for (const address of addresses) {
      const [host, prefix] = address.split('/');
      const bits = net.isIP(host) === 4 ? 32 : net.isIP(host) === 6 ? 128 : 0;
      if (!bits || !/^\d+$/.test(prefix ?? '') || Number(prefix) > bits) throw new Error('Некорректный адрес WireGuard');
    }
    try {
      await this.runner('ip', ['link', 'show', 'dev', interfaceName]);
      throw new Error('WireGuard-интерфейс уже занят; чужой интерфейс не изменён');
    } catch (error) { if ((error.code ?? error.exitCode) !== 1) throw error; }
    const child = this.spawnProcess('wireguard-go', ['--foreground', interfaceName], {
      env: { ...process.env, LOG_LEVEL: 'error' }, stdio: ['ignore', 'ignore', 'pipe'],
    });
    this.child = child;
    this.interfaceName = interfaceName;
    // Сообщения движка не попадают в API: конфигурация может содержать секреты.
    child.stderr?.resume();
    let failure;
    child.once('error', () => { failure = new Error('Не удалось запустить wireguard-go'); });
    child.once('exit', () => { failure ??= new Error('Процесс wireguard-go завершился'); });
    try {
      let ready = false;
      for (let i = 0; i < 100; i++) {
        if (failure) throw failure;
        try { await this.runner('wg', ['show', interfaceName]); ready = true; break; }
        catch (error) { if ((error.code ?? error.exitCode) !== 1) throw error; }
        await this.wait(25);
      }
      if (!ready) throw new Error('Userspace WireGuard не создал интерфейс вовремя');
      await this.runner('wg', ['setconf', interfaceName, strippedConfigPath]);
      for (const address of addresses) {
        await this.runner('ip', [address.includes(':') ? '-6' : '-4', 'address', 'add', address, 'dev', interfaceName]);
      }
      await this.runner('ip', ['link', 'set', 'dev', interfaceName, 'mtu', '1280', 'up']);
    } catch (error) {
      try { await this.down(interfaceName); }
      catch (rollbackError) { error.rollbackErrors = Object.freeze([rollbackError]); }
      throw error;
    }
  }

  async down(interfaceName) {
    if (!this.child || this.interfaceName !== interfaceName) throw new Error('Нет принадлежащего приложению процесса WireGuard');
    const child = this.child;
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      for (let i = 0; i < 100 && child.exitCode === null && child.signalCode === null; i++) await this.wait(25);
      if (child.exitCode === null && child.signalCode === null) throw new Error('Процесс WireGuard не остановлен; firewall сохранён');
    }
    try {
      await this.runner('ip', ['link', 'show', 'dev', interfaceName]);
      throw new Error('Интерфейс WireGuard остался после остановки; firewall сохранён');
    } catch (error) { if ((error.code ?? error.exitCode) !== 1) throw error; }
    this.child = null;
    this.interfaceName = null;
  }
}

module.exports = { UserspaceWireGuard };

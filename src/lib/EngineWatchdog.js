'use strict';

// Одинаково контролирует foreground WG и detached AWG после готовности сервиса.
class EngineWatchdog {
  constructor({ probe, onFailure, intervalMs = 2000, schedule = setInterval, cancel = clearInterval }) {
    this.probe = probe;
    this.onFailure = onFailure;
    this.intervalMs = intervalMs;
    this.schedule = schedule;
    this.cancel = cancel;
    this.running = false;
    this.checking = false;
  }

  start() {
    this.running = true;
    this.timer = this.schedule(() => this.check(), this.intervalMs);
    this.timer.unref?.();
  }

  async check() {
    if (!this.running || this.checking) return;
    this.checking = true;
    try {
      await this.probe();
    } catch {
      if (this.running) {
        this.stop();
        await this.onFailure(new Error('VPN engine недоступен; сервис остановлен для перезапуска'));
      }
    } finally {
      this.checking = false;
    }
  }

  stop() {
    this.running = false;
    if (this.timer) this.cancel(this.timer);
    this.timer = null;
  }
}

module.exports = { EngineWatchdog };

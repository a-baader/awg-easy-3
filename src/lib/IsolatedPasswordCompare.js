'use strict';

const path = require('node:path');
const { Worker } = require('node:worker_threads');

const unavailable = () => Object.assign(new Error('Проверка пароля временно недоступна. Повторите позже.'), {
  statusCode: 503, code: 'LOGIN_VERIFY_UNAVAILABLE',
});

const compareInWorker = async (password, hash, { timeoutMs = 5000 } = {}) => {
  let worker;
  try {
    worker = new Worker(path.join(__dirname, 'PasswordCompareWorker.js'), {
      workerData: { password, hash },
      resourceLimits: { maxOldGenerationSizeMb: 32, maxYoungGenerationSizeMb: 8, stackSizeMb: 2 },
    });
    return await new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => finish(undefined, unavailable()), timeoutMs);
      function finish(value, error) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error);
        else resolve(value);
      }
      worker.once('message', value => typeof value === 'boolean' ? finish(value) : finish(undefined, unavailable()));
      worker.once('error', () => finish(undefined, unavailable()));
      worker.once('exit', () => finish(undefined, unavailable()));
    });
  } catch {
    throw unavailable();
  } finally {
    // Слот ограничителя освобождается только после остановки worker.
    if (worker) await worker.terminate().catch(() => {});
  }
};

module.exports = { compareInWorker };

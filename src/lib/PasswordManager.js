'use strict';

const crypto = require('node:crypto');

const { generatePassword } = require('./BootstrapInstaller');
const { compareInWorker } = require('./IsolatedPasswordCompare');

const defaultHash = (password) => require('bcryptjs').hash(password, 12);
const defaultCompare = (password, hash) => require('bcryptjs').compare(password, hash);

const validatePassword = (password) => {
  if (typeof password !== 'string' || password.length < 12 || password.length > 128) {
    throw new TypeError('Пароль должен содержать от 12 до 128 символов и не более 72 байт UTF-8');
  }
  if (Buffer.byteLength(password, 'utf8') > 72) throw new TypeError('Пароль превышает предел bcrypt: 72 байта UTF-8');
  if (/[\0\r\n]/.test(password)) {
    throw new TypeError('Пароль не должен содержать нулевые байты или переводы строк');
  }
  return password;
};

class PasswordManager {
  constructor({
    store,
    hash = defaultHash,
    compare = defaultCompare,
    passwordGenerator = generatePassword,
    randomBytes = crypto.randomBytes,
    isReadOnly = () => false,
  } = {}) {
    if (!store || typeof store.load !== 'function' || typeof store.save !== 'function') {
      throw new TypeError('store must provide load and save methods');
    }
    for (const [name, dependency] of Object.entries({ hash, compare, passwordGenerator, randomBytes })) {
      if (typeof dependency !== 'function') throw new TypeError(`${name} must be a function`);
    }
    this.store = store;
    this.hash = hash;
    this.compare = compare;
    this.passwordGenerator = passwordGenerator;
    this.randomBytes = randomBytes;
    this.isReadOnly = isReadOnly;
  }

  async requireState() {
    const state = await this.store.load();
    if (!state) throw new Error('wg-dual-easy is not initialized');
    return state;
  }

  async verify(password, { auth, isolated = false } = {}) {
    // Сохраняем совместимость со старыми паролями до 128 символов UTF-8.
    if (typeof password !== 'string' || Buffer.byteLength(password, 'utf8') > 1024) return false;
    const expected = auth ?? (await this.requireState()).auth;
    const compare = isolated && this.compare === defaultCompare ? compareInWorker : this.compare;
    return Boolean(await compare(password, expected.passwordHash));
  }

  async replacePassword(state, newPassword) {
    validatePassword(newPassword);
    const passwordHash = await this.hash(newPassword);
    const sessionSecret = this.randomBytes(48).toString('base64url');
    return this.store.save({
      ...state,
      auth: { passwordHash, sessionSecret },
    });
  }

  async changePassword(currentPassword, newPassword) {
    return this.mutate(() => this.changePasswordLocked(currentPassword, newPassword));
  }

  mutate(operation) {
    const guarded = () => {
      if (this.isReadOnly()) throw Object.assign(new Error('Панель обслуживается; изменения временно недоступны'), { statusCode: 503, code: 'MAINTENANCE' });
      return operation();
    };
    return typeof this.store.serialize === 'function' ? this.store.serialize(guarded) : guarded();
  }

  async changePasswordLocked(currentPassword, newPassword) {
    const state = await this.requireState();
    if (typeof currentPassword !== 'string'
      || !(await this.compare(currentPassword, state.auth.passwordHash))) {
      throw new Error('Current password is incorrect');
    }
    await this.replacePassword(state, newPassword);
  }

  async resetPassword(newPassword = this.passwordGenerator()) {
    return this.mutate(async () => {
      const state = await this.requireState();
      await this.replacePassword(state, newPassword);
      return newPassword;
    });
  }
}

module.exports = {
  PasswordManager,
  validatePassword,
};

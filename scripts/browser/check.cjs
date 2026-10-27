'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const jsQR = require('jsqr');
const { PNG } = require('pngjs');
let step = 'запуск'; const secrets = []; const report = [];
const wait = async (condition, message, timeout = 12000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await condition()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw new Error(message);
};
(async () => {
  let input = ''; for await (const chunk of process.stdin) input += chunk;
  const options = JSON.parse(input);
  await fs.mkdir(options.artifacts, { recursive: true, mode: 0o700 });
  for (const target of options.targets) {
    secrets.push(target.password);
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], proxy: { server: target.proxy } });
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, deviceScaleFactor: 2, acceptDownloads: true });
      const page = await context.newPage(); const errors = [], csp = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error' && /Content Security Policy|Refused to/.test(message.text())) csp.push(message.text()); });
      page.on('dialog', dialog => dialog.accept());
      const login = async (tab, password) => {
        await tab.goto(target.url); await tab.locator('#login-view').waitFor({ state: 'visible' });
        await tab.locator('#password').fill(password); await tab.locator('#login-form button[type=submit]').click();
        await tab.locator('#app-view').waitFor({ state: 'visible' });
        await tab.locator('#clients .client-card').first().waitFor();
      };
      step = target.backend + ': вход'; await login(page, target.password);
      assert((await page.locator('#product-name').innerText()).includes(target.backend === 'wg' ? 'WG Easy Dual' : 'AWG'));
      const initialCount = await page.locator('#clients .client-card').count();
      step = target.backend + ': создание клиента';
      await page.locator('#show-create').click(); await page.locator('#client-name').fill('Браузерный клиент');
      await page.locator('#client-group').selectOption('guest'); await page.locator('#create-form button[type=submit]').click();
      await page.locator('#profile-dialog').waitFor({ state: 'visible' });
      await wait(async () => await page.locator('#clients .client-card').count() === initialCount + 1, 'Клиент не появился');
      step = target.backend + ': конфигурация и native QR';
      const pending = page.waitForEvent('download'); await page.locator('#download-config').click();
      const download = await pending; const native = await fs.readFile(await download.path(), 'utf8');
      assert(native.includes('[Interface]') && native.includes('PrivateKey = ') && native.includes('[Peer]'), 'Некорректный .conf');
      await page.locator('#show-native-qr').click();
      await wait(() => page.locator('#profile-qr-image').evaluate(image => image.complete && image.naturalWidth > 0), 'QR не отрисован');
      const bitmap = PNG.sync.read(await page.locator('#profile-qr-image').screenshot());
      const decoded = jsQR(new Uint8ClampedArray(bitmap.data), bitmap.width, bitmap.height);
      assert(decoded && decoded.data === native, 'Содержимое native QR отличается от .conf');
      if (target.backend === 'awg') {
        await page.locator('#show-vpn-qr').click();
        await wait(() => page.locator('#profile-qr-image').evaluate(image => image.complete && image.naturalWidth > 0), 'QR vpn:// не отрисован');
        // Ожидается новый blob после смены формата.
        await page.waitForTimeout(200);
        const vpnBitmap = PNG.sync.read(await page.locator('#profile-qr-image').screenshot());
        const vpn = jsQR(new Uint8ClampedArray(vpnBitmap.data), vpnBitmap.width, vpnBitmap.height);
        assert(vpn && vpn.data.startsWith('vpn://'), 'Не сохранён QR vpn://');
      } else {
        assert(await page.locator('#show-vpn-qr').isHidden() && await page.locator('#show-profile-link').isHidden(), 'В WG показан формат AWG');
      }
      await page.locator('#profile-dialog .close-dialog').click();
      await wait(() => page.locator('#profile-qr-image').evaluate(image => !image.getAttribute('src')), 'QR не очищен после закрытия');
      const card = () => page.locator('#clients .client-card').filter({ has: page.getByText('Браузерный клиент', { exact: true }) });
      step = target.backend + ': группы и разрешения';
      await card().locator('.group-toggle').locator('..').click();
      await wait(async () => await card().locator('.status').innerText() === 'Home', 'Группа не изменилась');
      await card().locator('.access-settings summary').click();
      await card().locator('.ipv4-toggle').locator('..').click();
      await wait(async () => !(await card().locator('.ipv4-toggle').isChecked()) && await card().locator('.ipv4-toggle').isEnabled(), 'IPv4 не отключён');
      await card().locator('.ipv4-toggle').locator('..').click();
      await wait(async () => await card().locator('.ipv4-toggle').isChecked() && await card().locator('.ipv4-toggle').isEnabled(), 'IPv4 не включён');
      step = target.backend + ': локализации и мобильный вид';
      for (const language of ['ru', 'fa', 'es', 'zh-cn', 'en']) {
        await page.locator('#language').selectOption(language);
        assert(await page.locator('html').getAttribute('lang') === language, 'Не применён язык');
        assert(await page.locator('html').getAttribute('dir') === (language === 'fa' ? 'rtl' : 'ltr'), 'Неверное направление текста');
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Горизонтальный overflow в мобильном виде');
      }
      await page.screenshot({ path: path.join(options.artifacts, target.backend + '-mobile.png'), fullPage: true });
      await fs.chmod(path.join(options.artifacts, target.backend + '-mobile.png'), 0o600);
      await page.setViewportSize({ width: 1440, height: 1050 });
      await page.screenshot({ path: path.join(options.artifacts, target.backend + '-desktop.png'), fullPage: true });
      await fs.chmod(path.join(options.artifacts, target.backend + '-desktop.png'), 0o600);
      step = target.backend + ': удаление';
      await card().locator('.delete-client').click(); await page.locator('#confirm-delete').click();
      await wait(async () => await page.locator('#clients .client-card').count() === initialCount, 'Клиент не удалён');
      const context2 = await browser.newContext(); const page2 = await context2.newPage(); await login(page2, target.password);
      step = target.backend + ': пароль и отзыв сессий';
      const newPassword = crypto.randomBytes(24).toString('base64url'); secrets.push(newPassword);
      await page.locator('details.settings').filter({ has: page.locator('#password-form') }).locator('summary').click();
      await page.locator('#current-password').fill(target.password); await page.locator('#new-password').fill(newPassword);
      await page.locator('#password-form button[type=submit]').click(); await page.locator('#login-view').waitFor({ state: 'visible' });
      assert(await page2.evaluate(async () => (await fetch('/api/v1/clients')).status) === 401, 'Старая сессия не отозвана');
      await login(page, newPassword);
      await page.locator('#logout').click(); await page.locator('#login-view').waitFor({ state: 'visible' });
      assert(errors.length === 0, 'Ошибки JavaScript в панели'); assert(csp.length === 0, 'Нарушение CSP');
      report.push({ backend: target.backend, result: 'passed', checks: ['login', 'create', 'conf', 'decoded-native-qr', 'backend-formats', 'group', 'ip-permissions', 'five-languages', 'rtl', 'mobile', 'delete', 'password-session-revocation', 'logout', 'js-csp'] });
      await context2.close(); await context.close();
      console.log('PASS: браузер ' + target.backend + ': UI, конфигурация/QR, языки, мобильный вид, CRUD, пароль/сессии');
    } finally { await browser.close(); }
  }
  await fs.writeFile(path.join(options.artifacts, 'report.json'), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
})().catch(error => {
  let message = String(error.message); for (const secret of secrets) message = message.replaceAll(secret, '[ПАРОЛЬ СКРЫТ]');
  message = message.replace(/[A-Za-z0-9+/]{43}=/g, '[КЛЮЧ СКРЫТ]').replace(/vpn:\/\/[^\s"']+/g, '[ПРОФИЛЬ СКРЫТ]');
  console.error('Ошибка браузерной проверки (' + step + '): ' + message); process.exitCode = 1;
});

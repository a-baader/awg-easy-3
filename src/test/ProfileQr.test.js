'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../www/js/profile-qr.js'), 'utf8');
function fixture(fetchText) {
  const context = { window: {}, Blob, URL };
  vm.runInNewContext(script, context);
  const changes = [], revoked = [], created = [];
  const image = { removeAttribute: name => changes.push('remove:' + name) };
  const container = { classList: { add: name => changes.push('hide:' + name), remove: name => changes.push('show:' + name) } };
  const qr = context.window.vpnProfileQr({ image, container, fetchText, translate: key => key,
    createUrl: blob => { created.push(blob); return 'blob:test-' + created.length; }, revokeUrl: url => revoked.push(url) });
  return { qr, image, created, revoked, changes };
}
test('QR использует отдельный формат и освобождает данные при закрытии', async () => {
  const calls = [];
  const f = fixture(async (id, format) => { calls.push([id, format]); return '<svg xmlns="http://www.w3.org/2000/svg"/>'; });
  await f.qr.show('phone', 'qr-native-svg');
  assert.deepEqual(calls, [['phone', 'qr-native-svg']]);
  assert.equal(f.image.alt, 'qrNativeAlt');
  assert.equal(f.image.src, 'blob:test-1');
  assert.equal(f.created[0].type, 'image/svg+xml');
  f.qr.clear();
  assert.deepEqual(f.revoked, ['blob:test-1']);
  await f.qr.show('phone', 'qr-svg');
  assert.equal(f.image.alt, 'qrVpnAlt');
});
test('ответ QR после закрытия окна не показывает секретный профиль', async () => {
  let finish;
  const f = fixture(() => new Promise(resolve => { finish = resolve; }));
  const pending = f.qr.show('phone', 'qr-native-svg');
  f.qr.clear(); finish('<svg/>'); await pending;
  assert.equal(f.created.length, 0);
  assert(!f.changes.includes('show:hidden'));
});
test('невалидный ответ не превращается в изображение QR', async () => {
  const f = fixture(async () => '<html>Ошибка</html>');
  await assert.rejects(f.qr.show('phone', 'qr-native-svg'), /qrError/);
  assert.equal(f.created.length, 0);
});

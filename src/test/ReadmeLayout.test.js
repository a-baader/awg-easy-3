'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const root = path.join(__dirname, '..', '..');

test('краткие README содержат два языка и ссылки на подробную документацию', () => {
  for (const file of ['README.md', 'README.ru.md']) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    for (const required of ['README.md', 'README.ru.md', 'WireGuard', 'HTTPS', 'docs/README.md', 'docs/TEST_REPORT.md']) {
      assert(text.includes(required), `${file}: отсутствует ${required}`);
    }
  }
  for (const file of ['README.es.md', 'README.fa.md', 'README.zh-CN.md']) {
    assert(!fs.existsSync(path.join(root, file)));
    assert(fs.existsSync(path.join(root, 'docs/archive/before-v0.2.0', file)));
  }
});

test('README следуют структуре оригинала и описывают вход в обе панели', () => {
  for (const [file, headings] of [
    ['README.md', ['What is wg-dual-easy?', 'At a glance', 'Installation and first login', 'Update and management', 'Technical features']],
    ['README.ru.md', ['Что такое wg-dual-easy', 'Кратко о возможностях', 'Установка и первый вход', 'Обновление и управление', 'Технические возможности']],
  ]) {
    const text = fs.readFileSync(path.join(root, file), 'utf8').replace(/\r\n/g, '\n');
    const positions = headings.map(h => text.indexOf(`## ${h}\n`));
    positions.forEach((p, i) => assert(p >= 0 && (i === 0 || p > positions[i - 1]), `${file}: порядок разделов`));
    assert.equal((text.slice(positions[1], positions[2]).match(/^- /gm) || []).length, 3);
    const install = text.slice(positions[2], positions[3]);
    for (const required of ['git clone', 'sudo ./vpnctl install', 'AmneziaVPN', 'vpn://', 'http://10.8.0.1:51821', 'http://10.9.0.1:51825', 'vpnctl client export']) {
      assert(install.includes(required), `${file}: отсутствует ${required}`);
    }
    for (const [, target] of text.matchAll(/\]\(([^)]+)\)/g)) {
      if (/^(https?:|#)/.test(target)) continue;
      assert(fs.existsSync(path.join(root, target.split('#')[0])), `${file}: отсутствует файл ${target}`);
    }
  }
});

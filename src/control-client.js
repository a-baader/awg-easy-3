'use strict';

const net = require('node:net');
const path = require('node:path');
const directory = process.env.VPN_RUNTIME_DIR ?? process.env.AWG_EASY_RUNTIME_DIR ?? '/run/awg-easy-3';
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input) > 16384) { console.error('Слишком большой запрос управления'); process.exit(64); }
});
process.stdin.on('end', () => {
  try { JSON.parse(input); } catch { console.error('Некорректный JSON запроса управления'); process.exitCode = 64; return; }
  const socket = net.connect(path.join(directory, 'control.sock'));
  socket.setEncoding('utf8');
  socket.setTimeout(30000, () => socket.destroy(new Error('Истёк срок ожидания управления')));
  let response = '';
  socket.on('connect', () => socket.write(input.trim() + '\n'));
  socket.on('data', chunk => {
    response += chunk.toString('utf8');
    if (Buffer.byteLength(response) > 4 * 1024 * 1024) socket.destroy(new Error('Слишком большой ответ управления'));
  });
  socket.on('end', () => { process.stdout.write(response); });
  socket.on('error', () => { console.error('Локальный канал управления недоступен'); process.exitCode = 1; });
});

'use strict';
// Только тестовый proxy внутри Home peer; разрешён один внутренний адрес панели.
const http = require('node:http');
const target = new URL(process.argv[2]);
const server = http.createServer((request, response) => {
  let url;
  try { url = new URL(request.url, target); } catch { response.writeHead(400).end(); return; }
  if (url.origin !== target.origin) { response.writeHead(403).end(); return; }
  const headers = { ...request.headers, host: target.host }; delete headers['proxy-connection'];
  const outgoing = http.request(url, { method: request.method, headers }, upstream => {
    response.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(response);
  });
  outgoing.on('error', () => { if (!response.headersSent) response.writeHead(502); response.end('Ошибка тестового proxy'); });
  request.on('error', () => outgoing.destroy()); request.pipe(outgoing);
});
server.on('connect', (_request, socket) => socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'));
server.listen(3128, '0.0.0.0', () => console.log('Тестовый Home proxy готов'));

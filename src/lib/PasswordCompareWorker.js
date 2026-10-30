'use strict';

const { parentPort, workerData } = require('node:worker_threads');

// Синхронный bcrypt занимает только выделенный worker, а не HTTP event loop.
parentPort.postMessage(require('bcryptjs').compareSync(workerData.password, workerData.hash));

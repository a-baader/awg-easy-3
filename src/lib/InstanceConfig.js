'use strict';

const net = require('node:net');
const path = require('node:path');
const { parseIPv4, parseIPv6 } = require('./AddressAllocator');

const DEFAULTS = Object.freeze({
  amneziawg: Object.freeze({ backend: 'amneziawg', interfaceName: 'awg0', ipv4Subnet: '10.8.0.0/24',
    address4: '10.8.0.1', firstClientAddress4: '10.8.0.2', listenPort: 51820, panelPort: 51821,
    tableName: 'awg_easy_3', containerName: 'awg-easy-3', dataDirectory: '/data', runtimeDirectory: '/run/awg-easy-3' }),
  wireguard: Object.freeze({ backend: 'wireguard', interfaceName: 'wg0', ipv4Subnet: '10.9.0.0/24',
    address4: '10.9.0.1', firstClientAddress4: '10.9.0.2', listenPort: 51824, panelPort: 51825,
    tableName: 'wg_easy_3', containerName: 'wg-easy-dual', dataDirectory: '/data', runtimeDirectory: '/run/wg-easy-dual' }),
});

const validateOwnedTable = value => {
  if (!['awg_easy_3', 'wg_easy_3'].includes(value)) throw new TypeError('Неизвестная таблица firewall проекта');
  return value;
};

const subnetRange = (value, family = 4) => {
  const match = typeof value === 'string' && value.match(/^(.+)\/(\d+)$/);
  const bits = family === 4 ? 32 : 128;
  if (!match || net.isIP(match[1]) !== family || Number(match[2]) > bits) throw new TypeError('Некорректная VPN-подсеть');
  const address = (family === 4 ? parseIPv4 : parseIPv6)(match[1]);
  const mask = (1n << BigInt(bits - Number(match[2]))) - 1n;
  return { start: address & ~mask, end: address | mask };
};
const subnetsOverlap = (left, right, family = 4) => {
  const a = subnetRange(left, family), b = subnetRange(right, family);
  return a.start <= b.end && b.start <= a.end;
};
const containsAddress = (subnet, address, family = 4) => subnetsOverlap(subnet, `${address}/${family === 4 ? 32 : 128}`, family);

const instanceConfig = (backend = 'amneziawg', overrides = {}) => {
  if (!Object.hasOwn(DEFAULTS, backend)) throw new TypeError('Неизвестный VPN backend');
  const config = { ...DEFAULTS[backend], ...overrides, backend };
  if (!/^[a-zA-Z0-9_.-]{1,15}$/.test(config.interfaceName)) throw new TypeError('Некорректное имя VPN-интерфейса');
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/.test(config.containerName)) throw new TypeError('Некорректное имя контейнера');
  validateOwnedTable(config.tableName);
  if (config.tableName !== DEFAULTS[backend].tableName) throw new TypeError('Таблица firewall принадлежит другому backend');
  for (const key of ['listenPort', 'panelPort']) {
    if (!Number.isInteger(config[key]) || config[key] < 1 || config[key] > 65535) throw new TypeError('Некорректный порт VPN');
  }
  for (const key of ['address4', 'firstClientAddress4']) {
    if (!containsAddress(config.ipv4Subnet, config[key])) throw new TypeError('VPN-адрес находится вне своей подсети');
  }
  for (const key of ['dataDirectory', 'runtimeDirectory']) {
    if (typeof config[key] !== 'string' || !path.isAbsolute(config[key]) || /[\0\r\n]/.test(config[key])) throw new TypeError('VPN-каталог должен быть абсолютным путём');
    config[key] = path.normalize(config[key]);
  }
  if (config.ipv6Subnet) subnetRange(config.ipv6Subnet, 6);
  return Object.freeze(config);
};

// Сравниваются host-пути mounts, а не одинаковое имя /data внутри контейнеров.
const validateInstances = (instances) => {
  for (let i = 0; i < instances.length; i++) for (const other of instances.slice(i + 1)) {
    const own = instances[i];
    for (const field of ['interfaceName', 'containerName', 'tableName', 'listenPort', 'panelPort']) {
      if (own[field] === other[field]) throw new TypeError(`Конфликт ресурсов VPN: ${field}`);
    }
    for (const field of ['hostDataDirectory', 'hostRuntimeDirectory']) {
      if (own[field] && other[field]) {
        const a = path.resolve(own[field]), b = path.resolve(other[field]);
        if (a === b || a.startsWith(b + path.sep) || b.startsWith(a + path.sep)) throw new TypeError(`Пересекаются каталоги VPN: ${field}`);
      }
    }
    if (subnetsOverlap(own.ipv4Subnet, other.ipv4Subnet)
      || own.ipv6Subnet && other.ipv6Subnet && subnetsOverlap(own.ipv6Subnet, other.ipv6Subnet, 6)) throw new TypeError('Пересекаются VPN-подсети');
  }
  return instances;
};

module.exports = { DEFAULTS, instanceConfig, validateOwnedTable, subnetRange, subnetsOverlap, containsAddress, validateInstances };

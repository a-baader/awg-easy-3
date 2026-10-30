'use strict';

// Синтетические политики для проверки настоящих UDP-пакетов в отдельной сети.
const { renderNftablesPolicy } = require('../../lib/NftablesPolicy');
const policies = {};
for (const [kind, n] of [['awg', 8], ['wg', 9]]) {
  policies[kind] = renderNftablesPolicy({
    interfaceName: `${kind}0`, tableName: `${kind}_easy_3`, wanInterface: 'wan0',
    ipv4Subnet: `10.${n}.0.0/24`, serverAddress4: `10.${n}.0.1`, isolation: true,
    home4: [`10.${n}.0.2`], guest4: [`10.${n}.0.4`],
  });
}
console.log(JSON.stringify(policies));

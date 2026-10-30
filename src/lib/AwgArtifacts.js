'use strict';

const { renderNftablesPolicy } = require('./NftablesPolicy');
const { clientTraffic } = require('./ClientTraffic');
const { createBackend, backendKind } = require('./VpnBackend');

const { buildClientArtifact, peerAddresses } = require('./ClientArtifact');

const buildAwgArtifacts = ({ server, clients, geoDatabase = {}, isolation = false }) => {
  if (!server || typeof server !== 'object' || Array.isArray(server)) {
    throw new TypeError('server must be an object');
  }
  if (!Array.isArray(clients) || clients.length === 0) {
    throw new TypeError('clients must be a non-empty array');
  }

  const enabledClients = clients.filter((client) => clientTraffic(client).enabled);
  const backend = createBackend(backendKind(server));
  const activeHomes = enabledClients.filter((client) => client.networkGroup === 'home');
  if (activeHomes.length === 0) throw new Error('At least one enabled home client is required');

  const serverHasIPv6 = Boolean(server.ipv6Subnet && server.address6);
  const serverAddresses = [
    `${server.address4}/${server.ipv4Prefix ?? 24}`,
    ...(serverHasIPv6 ? [`${server.address6}/${server.ipv6Prefix ?? 64}`] : []),
  ];

  const serverConfig = backend.renderServerConfig({
    privateKey: server.privateKey,
    addresses: serverAddresses,
    listenPort: server.listenPort,
    profile: server.profile,
    peers: enabledClients.map((client) => ({
      name: client.name,
      publicKey: client.publicKey,
      presharedKey: client.presharedKey,
      allowedIps: peerAddresses(client, serverHasIPv6),
    })),
  });

  const nftables = renderNftablesPolicy({
    tableName: backend.instance.tableName,
    isolation, serverAddress4: server.address4, serverAddress6: server.address6,
    geoDatabase,
    geoClients: enabledClients.map(client => ({
      policy: client.geoPolicy,
      address4: clientTraffic(client).ipv4Enabled ? client.address4 : undefined,
      address6: serverHasIPv6 && clientTraffic(client).ipv6Enabled ? client.address6 : undefined,
    })),
    interfaceName: server.interfaceName ?? 'awg0',
    wanInterface: server.wanInterface ?? 'eth0',
    ipv4Subnet: server.ipv4Subnet,
    ...(serverHasIPv6 ? { ipv6Subnet: server.ipv6Subnet } : {}),
    nat66: serverHasIPv6 && server.ipv6Mode === 'nat66',
    panelPort: server.panelPort ?? 51821,
    home4: activeHomes.filter((client) => clientTraffic(client).ipv4Enabled).map((client) => client.address4),
    guest4: enabledClients.filter((client) => client.networkGroup === 'guest' && clientTraffic(client).ipv4Enabled)
      .map((client) => client.address4),
    home6: serverHasIPv6 ? activeHomes.filter((client) => clientTraffic(client).ipv6Enabled)
      .map((client) => client.address6).filter(Boolean) : [],
    guest6: serverHasIPv6
      ? enabledClients.filter((client) => client.networkGroup === 'guest' && clientTraffic(client).ipv6Enabled)
        .map((client) => client.address6).filter(Boolean)
      : [],
  });

  const clientArtifacts = Object.fromEntries(clients.map(client =>
    [client.id, buildClientArtifact({ server, client, backend })]));

  return Object.freeze({
    clientArtifacts: Object.freeze(clientArtifacts),
    nftables,
    serverConfig,
    serverHasIPv6,
  });
};

module.exports = {
  buildAwgArtifacts,
};

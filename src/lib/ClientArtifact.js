'use strict';

const { createVpnLink } = require('./AmneziaVpnLink');
const { buildClientRoutes } = require('./ClientRoutes');
const { buildDnsPolicy } = require('./DnsPolicy');
const { createBackend, backendKind } = require('./VpnBackend');

const peerAddresses = (client, serverHasIPv6) => [
  `${client.address4}/32`,
  ...(serverHasIPv6 && client.address6 ? [`${client.address6}/128`] : []),
];

// Экспорт одного профиля не требует GeoIP, firewall или остальных клиентов.
const buildClientArtifact = ({ server, client, backend = createBackend(backendKind(server)) }) => {
  const serverHasIPv6 = Boolean(server.ipv6Subnet && server.address6);
  const routes = buildClientRoutes({ serverHasIPv6 });
  const dnsPolicy = buildDnsPolicy({ serverHasIPv6 });
  const addresses = peerAddresses(client, serverHasIPv6);
  const nativeConfig = backend.renderClientConfig({
    privateKey: client.privateKey,
    addresses,
    dnsPolicy,
    mtu: client.mtu ?? 1280,
    profile: server.profile,
    serverPublicKey: server.publicKey,
    presharedKey: client.presharedKey,
    allowedIps: routes.allowedIps,
    persistentKeepalive: client.persistentKeepalive ?? (backend.kind === 'wireguard' ? 25 : '25-35'),
    endpointHost: server.endpointHost,
    endpointPort: server.endpointPort ?? server.listenPort,
  });
  const vpnLink = backend.kind === 'wireguard' ? null : createVpnLink({
    description: client.name,
    hostName: server.endpointHost,
    port: server.endpointPort ?? server.listenPort,
    dns: dnsPolicy.amneziaDns,
    profile: server.profile,
    client: {
      addresses,
      privateKey: client.privateKey,
      publicKey: client.publicKey,
      serverPublicKey: server.publicKey,
      presharedKey: client.presharedKey,
      allowedIps: routes.allowedIps,
      persistentKeepalive: client.persistentKeepalive ?? '25-35',
      mtu: client.mtu ?? 1280,
      nativeConfig,
    },
  });

  return Object.freeze({ dnsPolicy, nativeConfig, routes, vpnLink });
};

module.exports = { buildClientArtifact, peerAddresses };

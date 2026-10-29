'use strict';

const net = require('node:net');
const { renderGeoIpPolicy } = require('./GeoIpPolicy');
const { validateOwnedTable } = require('./InstanceConfig');

const TABLE_NAME = 'awg_easy_3';

const buildAtomicNftBatch = (policy, { tableExists = false, tableName = TABLE_NAME } = {}) => {
  validateOwnedTable(tableName);
  if (typeof policy !== 'string' || !policy.includes(`table inet ${tableName} {`)) {
    throw new TypeError('Policy does not define the AWG-Easy 3 table');
  }
  if (/\bflush\s+ruleset\b/i.test(policy) || /\bdelete\s+table\b/i.test(policy)) {
    throw new TypeError('Policy contains a forbidden destructive statement');
  }
  const tables = [...policy.matchAll(/\btable\s+\w+\s+([a-zA-Z0-9_]+)\s*\{/g)];
  if (tables.length !== 1 || (policy.match(/\btable\s+/g) ?? []).length !== 1 || tables[0][1] !== tableName
      || /^\s*(?:add|insert|replace|flush|delete|include|destroy|rename|reset)\s/mi.test(policy)) {
    throw new TypeError('Политика затрагивает чужие ресурсы firewall');
  }
  const prefix = tableExists ? `delete table inet ${tableName}\n` : '';
  return `${prefix}${policy}`;
};

const validateInterface = (value) => {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9_.-]{1,15}$/.test(value)) {
    throw new TypeError('interfaceName must be a valid Linux interface name');
  }
  return value;
};

const validateAddress = (value, family, field) => {
  if (net.isIP(value) !== family) throw new TypeError(`${field} contains an invalid IPv${family} address: ${value}`);
  return value;
};

const validateCidr = (value, family, field) => {
  if (typeof value !== 'string') throw new TypeError(`${field} must be a CIDR string`);
  const separator = value.lastIndexOf('/');
  if (separator < 1) throw new TypeError(`${field} must be a CIDR string`);
  const address = value.slice(0, separator);
  const prefix = Number(value.slice(separator + 1));
  const maxPrefix = family === 4 ? 32 : 128;
  if (net.isIP(address) !== family || !Number.isInteger(prefix) || prefix < 0 || prefix > maxPrefix) {
    throw new TypeError(`${field} contains an invalid IPv${family} CIDR: ${value}`);
  }
  return value;
};

const validateAddressList = (value, family, field) => {
  if (!Array.isArray(value)) throw new TypeError(`${field} must be an array`);
  return [...new Set(value.map((address) => validateAddress(address, family, field)))].sort();
};

const quote = (value) => `"${value}"`;
const elementsClause = (values) => values.length === 0
  ? ''
  : `\n    elements = { ${values.join(', ')} }`;

const renderNftablesPolicy = ({
  interfaceName = 'awg0',
  wanInterface = 'eth0',
  ipv4Subnet = '10.8.0.0/24',
  ipv6Subnet,
  nat66 = false,
  home4,
  guest4,
  home6 = [],
  guest6 = [],
  panelPort = 51821,
  geoClients = [],
  geoDatabase = {},
  tableName = TABLE_NAME,
  isolation = false,
  serverAddress4,
  serverAddress6,
}) => {
  validateOwnedTable(tableName);
  const awg = validateInterface(interfaceName);
  const wan = validateInterface(wanInterface);
  const subnet4 = validateCidr(ipv4Subnet, 4, 'ipv4Subnet');
  const subnet6 = ipv6Subnet === undefined ? undefined : validateCidr(ipv6Subnet, 6, 'ipv6Subnet');
  const normalizedHome4 = validateAddressList(home4, 4, 'home4');
  const normalizedGuest4 = validateAddressList(guest4, 4, 'guest4');
  const normalizedHome6 = validateAddressList(home6, 6, 'home6');
  const normalizedGuest6 = validateAddressList(guest6, 6, 'guest6');
  if (typeof nat66 !== 'boolean') throw new TypeError('nat66 must be a boolean');
  if (nat66 && !subnet6) throw new TypeError('nat66 requires ipv6Subnet');

  if (normalizedHome4.length === 0 && (!subnet6 || normalizedHome6.length === 0)) {
    throw new TypeError('At least one home peer with a permitted IP family is required');
  }
  if (!subnet6 && (normalizedHome6.length || normalizedGuest6.length)) {
    throw new TypeError('IPv6 peers require ipv6Subnet');
  }
  const overlaps = normalizedHome4.filter((address) => normalizedGuest4.includes(address));
  if (overlaps.length > 0) throw new TypeError(`Peers cannot be both home and guest: ${overlaps.join(', ')}`);
  const overlaps6 = normalizedHome6.filter((address) => normalizedGuest6.includes(address));
  if (overlaps6.length > 0) throw new TypeError(`IPv6 peers cannot be both home and guest: ${overlaps6.join(', ')}`);

  const port = Number(panelPort);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new TypeError('panelPort must be an integer between 1 and 65535');
  }

  const ipv6Sets = subnet6 ? `
  set active6 {
    type ipv6_addr${elementsClause([...normalizedHome6, ...normalizedGuest6])}
  }

  set home6 {
    type ipv6_addr${elementsClause(normalizedHome6)}
  }

  set guest6 {
    type ipv6_addr${elementsClause(normalizedGuest6)}
  }

` : '';

  const ipv6InputRules = subnet6 ? `
    iifname ${quote(awg)} ip6 saddr @guest6 ip6 daddr ${subnet6} drop comment "guest peers cannot access AWG-Easy 3 services"
    iifname ${quote(awg)} ip6 saddr @home6 tcp dport ${port} accept comment "home peers may access the panel"` : '';

  const ipv6HomeForwardRule = subnet6 ? `
    iifname ${quote(awg)} oifname ${quote(awg)} ip6 saddr @home6 ip6 daddr @home6 accept comment "home IPv6 peer traffic"` : '';

  const ipv6ForwardRules = subnet6 ? `
    iifname ${quote(awg)} oifname ${quote(wan)} ip6 saddr ${subnet6} accept comment "AWG IPv6 to WAN"
    iifname ${quote(wan)} oifname ${quote(awg)} ip6 daddr ${subnet6} ct state established,related accept comment "return IPv6 traffic"` : '';

  const ipv6NatRule = nat66
    ? `\n    oifname ${quote(wan)} ip6 saddr ${subnet6} masquerade comment "AWG-Easy 3 IPv6 NAT"`
    : '';

  const ipv6PermissionRules = subnet6 ? `
    iifname ${quote(awg)} ip6 saddr != @active6 drop comment "IPv6 permission: from VPN client"
    oifname ${quote(awg)} ip6 daddr != @active6 drop comment "IPv6 permission: to VPN client"` : `
    iifname ${quote(awg)} meta nfproto ipv6 drop comment "IPv6 unavailable"
    oifname ${quote(awg)} meta nfproto ipv6 drop comment "IPv6 unavailable"`;

  const geo = renderGeoIpPolicy({ clients: geoClients, database: geoDatabase, awg, wan,
    active4: [...normalizedHome4, ...normalizedGuest4], active6: [...normalizedHome6, ...normalizedGuest6] });

  const legacyInput = `
    iifname ${quote(awg)} ip saddr @guest4 ip daddr ${subnet4} drop comment "guest peers cannot access AWG-Easy 3 services"
    iifname ${quote(awg)} ip saddr @home4 tcp dport ${port} accept comment "home peers may access the panel"${ipv6InputRules}`;
  let strictInput = '';
  if (isolation) {
    validateAddress(serverAddress4, 4, 'serverAddress4');
    if (subnet6) validateAddress(serverAddress6, 6, 'serverAddress6');
    strictInput = `
    ip daddr ${subnet4} iifname != ${quote(awg)} iifname != "lo" drop comment "deny foreign access to VPN host"
    iifname ${quote(awg)} ip saddr @home4 ip daddr ${serverAddress4} tcp dport ${port} accept comment "own Home panel"
    iifname ${quote(awg)} ip saddr @home4 ip daddr ${serverAddress4} icmp type echo-request accept comment "own Home diagnostics"
    iifname ${quote(awg)} ip saddr @home4 ip daddr { ${serverAddress4}, 224.0.0.251, 239.255.255.250 } udp dport { 5353, 1900 } accept comment "own Home discovery"${subnet6 ? `
    ip6 daddr ${subnet6} iifname != ${quote(awg)} iifname != "lo" drop comment "deny foreign IPv6 access to VPN host"
    iifname ${quote(awg)} ip6 saddr @home6 ip6 daddr ${serverAddress6} tcp dport ${port} accept comment "own Home IPv6 panel"
    iifname ${quote(awg)} ip6 saddr @home6 ip6 daddr ${serverAddress6} icmpv6 type echo-request accept comment "own Home IPv6 diagnostics"` : ''}
    iifname ${quote(awg)} drop comment "deny VPN access to other host services"`;
  }
  const privateDestinations = isolation ? `
    iifname ${quote(awg)} ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 } drop comment "deny private and host-service forwarding"
    iifname ${quote(awg)} ip6 daddr { ::/96, ::ffff:0:0/96, fc00::/7, fe80::/10, ff00::/8, 2001:db8::/32 } drop comment "deny private IPv6 forwarding"` : '';
  const forwardBoundary = isolation ? `
    iifname ${quote(awg)} drop comment "deny other VPN egress"
    oifname ${quote(awg)} drop comment "deny unsolicited VPN ingress"` : '';

  return `# Managed by AWG-Easy 3. Do not append unrelated rules to this table.
table inet ${tableName} {
  set active4 {
    type ipv4_addr${elementsClause([...normalizedHome4, ...normalizedGuest4])}
  }

  set home4 {
    type ipv4_addr${elementsClause(normalizedHome4)}
  }

  set guest4 {
    type ipv4_addr${elementsClause(normalizedGuest4)}
  }
${ipv6Sets}${geo.sets}
${isolation ? `
  chain discovery_prerouting {
    type nat hook prerouting priority dstnat; policy accept;
    iifname ${quote(awg)} ip saddr @home4 ip daddr 224.0.0.251 udp dport 5353 dnat ip to ${serverAddress4} comment "own VPN mDNS"
    iifname ${quote(awg)} ip saddr @home4 ip daddr 239.255.255.250 udp dport 1900 dnat ip to ${serverAddress4} comment "own VPN SSDP"
  }
` : ''}
  chain client_permissions {
    iifname ${quote(awg)} ip saddr != @active4 drop comment "IPv4 permission: from VPN client"
    oifname ${quote(awg)} ip daddr != @active4 drop comment "IPv4 permission: to VPN client"${ipv6PermissionRules}${isolation ? `
    iifname ${quote(awg)} ct status dnat drop comment "Запрет доступа VPN через DNAT"` : ''}
  }

  chain input {
    type filter hook input priority -10; policy accept;
    jump client_permissions${isolation ? strictInput : legacyInput}
  }

  chain forward {
    type filter hook forward priority -10; policy accept;
    jump client_permissions
    iifname ${quote(awg)} oifname ${quote(awg)} ip saddr @home4 ip daddr @home4 accept comment "home peer traffic"${ipv6HomeForwardRule}
    iifname ${quote(awg)} oifname ${quote(awg)} drop comment "isolate guest peers"${privateDestinations}
${geo.rules ? `${geo.rules}\n` : ''}    iifname ${quote(awg)} oifname ${quote(wan)} ip saddr ${subnet4} accept comment "AWG IPv4 to WAN"
    iifname ${quote(wan)} oifname ${quote(awg)} ip daddr ${subnet4} ct state established,related accept comment "return IPv4 traffic"${ipv6ForwardRules}${forwardBoundary}
  }

  chain output {
    type filter hook output priority -10; policy accept;
    jump client_permissions
  }

  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    oifname ${quote(wan)} ip saddr ${subnet4} masquerade comment "AWG-Easy 3 IPv4 NAT"${ipv6NatRule}
  }
}
`;
};

module.exports = {
  TABLE_NAME,
  buildAtomicNftBatch,
  renderNftablesPolicy,
};

"""Настоящий multicast и границы DNAT. Только внутри пустого unshare --net."""
import json
import os
import socket
import subprocess
import sys
import time


def run(*args, data=None):
    return subprocess.check_output(args, input=data, text=True)


if (os.geteuid() != 0
        or os.readlink('/proc/self/ns/net') == os.readlink('/proc/1/ns/net')
        or {item['ifname'] for item in json.loads(run('ip', '-j', 'link', 'show'))} != {'lo'}):
    sys.exit('Проверка требует отдельного пустого network namespace и root')

policies = json.load(sys.stdin)
children = []
peers = {}
checks = 0
try:
    run('ip', 'link', 'set', 'lo', 'up')
    for kind, interface, network in [('awg', 'awg0', '10.8.0'), ('wg', 'wg0', '10.9.0'), ('wan', 'wan0', '192.0.2')]:
        child = subprocess.Popen(['unshare', '--net', 'sleep', '120'])
        children.append(child)
        for attempt in range(100):
            if os.readlink(f'/proc/{child.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                break
            time.sleep(.01)
        else:
            raise RuntimeError('Не создан network namespace клиента')
        run('ip', 'link', 'add', interface, 'type', 'veth', 'peer', 'name', 'peer0')
        run('ip', 'link', 'set', 'peer0', 'netns', str(child.pid))
        run('ip', 'addr', 'add', network + '.1/24', 'dev', interface)
        run('ip', 'link', 'set', interface, 'up')
        prefix = ['nsenter', '-t', str(child.pid), '-n']
        peers[kind] = prefix
        for suffix in [2, 4, 5]:
            run(*prefix, 'ip', 'addr', 'add', f'{network}.{suffix}/24', 'dev', 'peer0')
        run(*prefix, 'ip', 'link', 'set', 'peer0', 'up')
        run(*prefix, 'ip', 'route', 'add', 'default', 'via', network + '.1')
        run(*prefix, 'ip', 'route', 'add', '224.0.0.0/4', 'dev', 'peer0')
    run('nft', '-f', '-', data='\n'.join(policies.values()))

    def probe(sender, source, destination, port, receiver, expected, label):
        global checks
        payload = ('Проверка-' + label).encode()
        script = ('import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);'
                  f's.bind(({source!r},0));'
                  f's.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton({source!r}));'
                  f's.sendto({payload!r},({destination!r},{port}))')
        run(*peers[sender], sys.executable, '-c', script)
        try:
            received = receiver.recvfrom(4096)[0]
        except socket.timeout:
            received = None
        assert (received == payload) == expected, 'Нарушение политики: ' + label
        checks += 1

    for kind, n in [('awg', 8), ('wg', 9)]:
        address = f'10.{n}.0.1'
        for group, port in [('224.0.0.251', 5353), ('239.255.255.250', 1900)]:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
                receiver.bind((address, port))
                receiver.settimeout(.2)
                for destination in [address, group]:
                    for suffix, permitted in [(2, True), (4, False), (5, False)]:
                        probe(kind, f'10.{n}.0.{suffix}', destination, port, receiver, permitted,
                              f'{kind}-{destination}-{port}-{suffix}')
                probe('wan', '192.0.2.2', address, port, receiver, False, f'{kind}-WAN-{port}')
                # Чужой DNAT с тем же конечным адресом/портом не получает исключение.
                run('nft', '-f', '-', data=f'''table ip attack {{
 chain pre {{ type nat hook prerouting priority -101; policy accept;
  iifname "{kind}0" ip daddr 198.51.100.10 udp dport {port} dnat to {address}:{port}
  iifname "{kind}0" ip daddr {group} udp dport 9999 dnat to {address}:{port}
 }}
}}
''')
                probe(kind, f'10.{n}.0.2', '198.51.100.10', port, receiver, False, f'{kind}-foreign-DNAT-{port}')
                probe(kind, f'10.{n}.0.2', group, 9999, receiver, False, f'{kind}-wrong-original-port-{port}')
                run('nft', 'delete', 'table', 'ip', 'attack')
        run('nft', 'delete', 'element', 'inet', kind + '_easy_3', 'active4', '{', f'10.{n}.0.2', '}')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind((address, 5353)); receiver.settimeout(.2)
            probe(kind, f'10.{n}.0.2', '224.0.0.251', 5353, receiver, False, f'{kind}-revoked-Home')
    print(f'PASS: {checks} проверок multicast/unicast mDNS/SSDP, Home/Guest/WAN, DNAT и отзыва доступа')
finally:
    for child in children:
        child.terminate()
        child.wait(timeout=5)

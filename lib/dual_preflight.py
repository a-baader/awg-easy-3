"""Проверки Dual deployment; функции не изменяют ресурсы хоста."""
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess

class PreflightError(RuntimeError):
    """Небезопасное или неподдерживаемое окружение."""

OWNERS = {'awg': ('amneziawg', 'awg_easy_3'), 'wg': ('wireguard', 'wg_easy_3')}

def validate_instances(instances):
    if not instances or set(instances) - set(OWNERS):
        raise PreflightError('Неизвестный компонент VPN')
    for name, item in instances.items():
        backend, table = OWNERS[name]
        if item.get('backend') != backend or item.get('table') != table:
            raise PreflightError('Неверный владелец VPN-ресурсов')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', str(item.get('interface', ''))):
            raise PreflightError('Некорректный VPN-интерфейс')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', str(item.get('container', ''))):
            raise PreflightError('Некорректное имя VPN-контейнера')
        for key in ('udp_port', 'panel_port'):
            if type(item.get(key)) is not int or not 1 <= item[key] <= 65535:
                raise PreflightError('Некорректный порт VPN')
        try:
            network = ipaddress.IPv4Network(item['network4'], strict=True)
            address = ipaddress.IPv4Address(item['address4'])
            if network.prefixlen > 30 or address not in network or address in (network.network_address, network.broadcast_address):
                raise ValueError()
            if item.get('network6'):
                network6 = ipaddress.IPv6Network(item['network6'], strict=True)
                if ipaddress.IPv6Address(item['address6']) not in network6:
                    raise ValueError()
        except (ValueError, KeyError):
            raise PreflightError('Некорректные адреса или VPN-подсети') from None
        data = item.get('data_directory', '')
        if not isinstance(data, str) or not os.path.isabs(data) or Path(data).resolve() in (Path('/'), Path('/etc'), Path('/run'), Path('/var')):
            raise PreflightError('Небезопасный каталог данных VPN')
    entries = list(instances.values())
    for i, own in enumerate(entries):
        for other in entries[i + 1:]:
            for key in ('interface', 'table', 'container', 'udp_port', 'panel_port'):
                if own[key] == other[key]:
                    raise PreflightError('Пересекаются ресурсы VPN: ' + key)
            left, right = Path(own['data_directory']).resolve(), Path(other['data_directory']).resolve()
            if left == right or left in right.parents or right in left.parents:
                raise PreflightError('Пересекаются каталоги данных VPN')
            if ipaddress.ip_network(own['network4']).overlaps(ipaddress.ip_network(other['network4'])):
                raise PreflightError('Пересекаются IPv4-подсети VPN')
            if own.get('network6') and other.get('network6') and ipaddress.ip_network(own['network6']).overlaps(ipaddress.ip_network(other['network6'])):
                raise PreflightError('Пересекаются IPv6-подсети VPN')
    return instances

def execute(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise PreflightError('Не выполнена проверка: ' + args[0])
    return result.stdout

def environment_check(run=execute):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise PreflightError('Требуется нативный Linux/amd64')
    if 'microsoft' in platform.release().lower():
        raise PreflightError('WSL не поддерживается')
    tun = Path('/dev/net/tun')
    if not tun.exists() or not stat.S_ISCHR(tun.stat().st_mode):
        raise PreflightError('Устройство /dev/net/tun недоступно')
    for binary in ('docker', 'ip', 'ss', 'nft'):
        if not shutil.which(binary):
            raise PreflightError('Не найдена обязательная команда: ' + binary)
    info = json.loads(run(['docker', 'info', '--format', '{{json .}}']))
    if info.get('OSType') != 'linux' or info.get('Architecture') not in ('x86_64', 'amd64'):
        raise PreflightError('Docker daemon должен работать на Linux/amd64')
    if any('rootless' in str(option) for option in info.get('SecurityOptions', [])):
        raise PreflightError('Rootless Docker не поддерживает требуемую host networking модель')
    version = run(['docker', 'compose', 'version', '--short']).strip().lstrip('v')
    if not version.startswith('2.'):
        raise PreflightError('Требуется Docker Compose v2')
    return {'docker': info.get('ServerVersion'), 'compose': version}

def inspect_conflicts(instances, *, owned=None, run=execute):
    """owned содержит только доказанное соответствие container/mount/state."""
    validate_instances(instances)
    owned = owned or {}
    routes = {}
    for family in (4, 6):
        routes[family] = json.loads(run(['ip', '-j', '-' + str(family), 'route', 'show', 'table', 'all']))
    links = json.loads(run(['ip', '-j', 'link', 'show']))
    names = {link['ifname'] for link in links}
    tables = json.loads(run(['nft', '-j', 'list', 'tables']))
    existing_tables = {(item['table']['family'], item['table']['name']) for item in tables.get('nftables', []) if 'table' in item}
    raw_containers = run(['docker', 'ps', '-a', '--format', '{{json .}}']).strip()
    try:
        containers = json.loads(raw_containers or '[]')
    except json.JSONDecodeError:
        containers = [json.loads(line) for line in raw_containers.splitlines()]
    if isinstance(containers, dict):
        containers = [containers]
    container_names = {item.get('Names') for item in containers}
    published = []
    for container in containers:
        ports = json.loads(run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', container['ID']]) or 'null') or {}
        for key, bindings in ports.items():
            port_protocol = key.rsplit('/', 1)[-1]
            for binding in bindings or []:
                published.append((container.get('Names'), port_protocol, int(binding['HostPort'])))
    for name, item in instances.items():
        proof = owned.get(name)
        if proof and (proof.get('container') != item['container'] or proof.get('data_directory') != item['data_directory']):
            raise PreflightError('Не подтверждено владение существующей установкой')
        if item['interface'] in names and not proof:
            raise PreflightError('VPN-интерфейс уже занят: ' + item['interface'])
        if ('inet', item['table']) in existing_tables and not proof:
            raise PreflightError('Таблица firewall уже занята: ' + item['table'])
        if item['container'] in container_names and not proof:
            raise PreflightError('Имя контейнера уже занято: ' + item['container'])
        for container, protocol, port in published:
            if container != item['container'] and (protocol == 'udp' and port == item['udp_port'] or protocol == 'tcp' and port == item['panel_port']):
                raise PreflightError('Порт опубликован другим Docker-контейнером: ' + str(port))
        for family in (4, 6):
            subnet = item.get('network' + str(family))
            if not subnet:
                continue
            own_network = ipaddress.ip_network(subnet)
            for route in routes[family]:
                destination = route.get('dst', 'default')
                if destination in ('default', '0.0.0.0/0', '::/0'):
                    continue
                try:
                    other = ipaddress.ip_network(destination, strict=False)
                except ValueError:
                    raise PreflightError('Не удалось проверить существующий маршрут') from None
                if proof and route.get('dev') == item['interface'] and other.subnet_of(own_network):
                    continue
                if own_network.overlaps(other):
                    raise PreflightError('VPN-подсеть пересекается с маршрутом: ' + subnet)
        for protocol, port in [('udp', item['udp_port']), ('tcp', item['panel_port'])]:
            flags = '-Hlun' if protocol == 'udp' else '-Hltn'
            occupied = run(['ss', flags, 'sport = :' + str(port)]).strip()
            if occupied and not proof:
                raise PreflightError('Порт уже занят: ' + str(port) + '/' + protocol)
    return {'conflicts': False, 'components': list(instances)}

"""Проверка stable-релиза после загрузки; сетевые данные никогда не исполняются."""
import hashlib
import json
import re
import shutil
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from vpnctl_common import ManagerError, atomic_json

REPOSITORY='a-baader/awg-easy-3'

def backend(root):
    if (root/'run/systemd/system').is_dir() and shutil.which('systemctl'):return 'systemd'
    if (root/'etc/init.d').is_dir() and all(shutil.which(tool) for tool in ('rc-update','rc-service','openrc-run')):return 'openrc'
    return 'unsupported'

def files(root,kind):
    if kind=='systemd':return {
        root/'etc/systemd/system/vpn-easy-dual-update.service':('''[Unit]
Description=Проверка стабильного обновления VPN Easy Dual
Wants=network-online.target
After=network-online.target docker.service
ConditionPathExists=/etc/vpn-easy-dual/config.json

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vpnctl auto-update run
TimeoutStartSec=15min
TimeoutStopSec=3min
''',0o644),
        root/'etc/systemd/system/vpn-easy-dual-update.timer':('''[Unit]
Description=Проверка стабильного обновления VPN Easy Dual после загрузки

[Timer]
OnActiveSec=5min
RandomizedDelaySec=2min
AccuracySec=1min
Persistent=false
Unit=vpn-easy-dual-update.service

[Install]
WantedBy=timers.target
''',0o644)}
    if kind=='openrc':return {
        root/'etc/init.d/vpn-easy-dual-update':('''#!/sbin/openrc-run
description="Проверка стабильного обновления VPN Easy Dual после загрузки"
command="/usr/local/libexec/vpn-easy-dual-update-on-boot"
command_background="yes"
pidfile="/run/vpn-easy-dual-update.pid"
depend() {
  need net
  after docker
}
''',0o755),
        root/'usr/local/libexec/vpn-easy-dual-update-on-boot':('''#!/bin/sh
# Однократная проверка через пять минут после запуска ОС.
exec >> /var/log/vpn-easy-dual-update.log 2>&1
sleep 300
update_status=0
/usr/local/sbin/vpnctl auto-update run || update_status=$?
printf 'Проверка обновления завершена с кодом %s.\\n' "$update_status"
pidfile=/run/vpn-easy-dual-update.pid
if [ -r "$pidfile" ] && [ "$(cat "$pidfile")" = "$$" ]; then
  rc-service --nodeps vpn-easy-dual-update zap >/dev/null 2>&1 || true
fi
exit "$update_status"
''',0o755)}
    return {}

def configure(manager,enabled,config=None):
    kind=backend(manager.system_root)
    # Файлы должны пройти проверку владения до управления службой.
    records=config if config is not None else manager.config(False) or {}
    for path in files(manager.system_root,kind):
        digest=records.get('manager_files',{}).get(str(path))
        if not enabled and not path.exists() and not path.is_symlink():continue
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ManagerError('Не подтверждено владение файлом boot updater: '+str(path),73)
    if kind=='systemd':
        manager.run(['systemctl','daemon-reload'])
        manager.run(['systemctl','enable','--now','vpn-easy-dual-update.timer'] if enabled else ['systemctl','disable','--now','vpn-easy-dual-update.timer'],check=enabled)
    elif kind=='openrc':
        manager.run(['rc-update','add' if enabled else 'del','vpn-easy-dual-update','default'],check=enabled)
        manager.run(['rc-service','vpn-easy-dual-update','start' if enabled else 'stop'],check=enabled)
    return {'backend':kind,'enabled':enabled and kind!='unsupported'}

def status(manager):
    requested=manager.config().get('auto_check',False);kind=backend(manager.system_root)
    enabled=False
    if requested and kind=='systemd':
        enabled=(manager.run(['systemctl','is-enabled','vpn-easy-dual-update.timer'],check=False) or '').strip()=='enabled'
    elif requested and kind=='openrc':
        value=manager.run(['rc-update','show','default'],check=False) or ''
        enabled=any(line.split() and line.split()[0]=='vpn-easy-dual-update' for line in value.splitlines())
    return {'requested':requested,'enabled':enabled,'backend':kind}

def download(url,limit=1024*1024):
    request=Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'vpnctl'})
    with urlopen(request,timeout=30) as response:
        value=response.read(limit+1)
    if len(value)>limit:raise ManagerError('Ответ сервера релизов слишком большой',65)
    return value

def stable_version(value):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-rc\.([1-9]\d*))?',value)
    if not match:raise ManagerError('Некорректная версия релиза',65)
    return tuple(int(match[i]) for i in (1,2,3))+(0 if match[4] else 1,)

def fetch_release(current,fetch=download):
    try:release=json.loads(fetch('https://api.github.com/repos/'+REPOSITORY+'/releases/latest'))
    except HTTPError as error:
        if error.code==404:return None
        raise
    tag=release.get('tag_name','')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+',tag):
        raise ManagerError('Сервер не вернул стабильный опубликованный релиз',65)
    if stable_version(tag[1:])<=stable_version(current):return None
    return fetch_manifest(tag[1:],fetch)

def fetch_manifest(version,fetch=download):
    stable_version(version)
    base='https://github.com/'+REPOSITORY+'/releases/download/v'+version+'/'
    manifest=fetch(base+'release-manifest.json')
    checksums=fetch(base+'SHA256SUMS').decode()
    expected=hashlib.sha256(manifest).hexdigest()
    if not any(re.fullmatch(expected+r'\s+\*?release-manifest\.json',line) for line in checksums.splitlines()):
        raise ManagerError('Контрольная сумма манифеста релиза не совпала',65)
    value=json.loads(manifest)
    if value.get('version')!=version or value.get('platform')!='linux/amd64' or not re.fullmatch(r'[a-f0-9]{40}',value.get('source_commit','')):
        raise ManagerError('Манифест не соответствует выбранному релизу',65)
    if set(value.get('images',{}))!={'awg','wg'} or any(not re.fullmatch('ghcr.io/a-baader/awg-easy-3-'+name+r'@sha256:[a-f0-9]{64}',image) for name,image in value['images'].items()):
        raise ManagerError('Манифест содержит неподходящие образы',65)
    return value

def run(manager):
    config=manager.config()
    if not config.get('auto_check',False):return {'skipped':'проверка отключена'}
    try:
        current=min((item.get('version',config['version']) for item in config['components'].values()),key=stable_version)
        release=fetch_release(current)
    except Exception as error:
        # Ответ GitHub и сетевые ошибки не должны попадать в журнал как traceback.
        raise ManagerError('Не удалось проверить stable-релиз; установленная версия сохранена',69) from error
    if release is None:return {'updated':False,'reason':'нового стабильного релиза нет'}
    selected=[name for name,item in config['components'].items()
        if stable_version(item.get('version',config['version']))<stable_version(release['version'])]
    if not selected:return {'updated':False,'reason':'компоненты уже обновлены'}
    path=manager.root/'stable-release.json';atomic_json(path,release)
    from types import SimpleNamespace
    component='all' if len(selected)==len(config['components']) else selected[0]
    return manager.update(SimpleNamespace(component=component,manifest=str(path),dry_run=False))

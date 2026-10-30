"""Обзор и явно подтверждаемая очистка старых ресурсов; автоматического удаления нет."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import time

from vpnctl_backup import verify_archive
from vpnctl_common import ManagerError
import vpnctl_purge
import vpnctl_sources


def size(path):
    if path.is_symlink():return 0
    if path.is_file():return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file() and not item.is_symlink())


def snapshot_owned(path, boundary):
    vpnctl_sources.trusted(path,boundary)
    record=json.loads((path/'_vpnctl_snapshot.json').read_text())
    if record.get('format')!=1 or not isinstance(record.get('files'),dict):return False
    actual={str(item.relative_to(path)) for item in path.rglob('*') if item.is_file()
            and '__pycache__' not in item.relative_to(path).parts and item.name!='_vpnctl_snapshot.json'}
    if actual!=set(record['files']):return False
    for name,metadata in record['files'].items():
        file=path/name
        if file.resolve()!=file or path not in file.parents:return False
        vpnctl_sources.trusted(file,boundary)
        if hashlib.sha256(file.read_bytes()).hexdigest()!=metadata['sha256']:return False
    return True


def inspect(manager):
    config=manager.config(False)
    vpnctl_purge.check_paths(manager,config)
    protected={manager.project.resolve()}
    marker=manager.system_root/'etc/vpn-easy-dual-install-dir'
    if marker.is_file() and not marker.is_symlink():protected.add(Path(marker.read_text().strip()).resolve())
    if config:protected.add(Path(config['project']).resolve())
    if manager.journal.exists():
        journal=json.loads(manager.journal.read_text())
        if journal.get('snapshot'):protected.add(Path(journal['snapshot']).resolve())
    items=[]
    groups=[('backup',manager.root/'backups'),('source',manager.root/'sources'),
            ('snapshot',manager.system_root/'usr/local/lib/wg-dual-easy/sources'),('release',manager.root/'releases')]
    for kind,root in groups:
        if not root.exists():continue
        if root.is_symlink() or root.resolve()!=root:raise ManagerError('Небезопасный каталог хранения',73)
        for path in root.iterdir():
            owned=False
            try:
                if path.is_symlink():continue
                if kind=='backup' and path.is_file() and re.fullmatch(r'backup-\d{8}T\d{6}-[a-f0-9]{8}\.tar\.gz',path.name):
                    verify_archive(path);owned=True
                elif kind=='source' and path.is_dir() and re.fullmatch(r'v\d+\.\d+\.\d+(?:-rc\.\d+)?-[a-f0-9]{40}',path.name):
                    commit=manager.run(['git','-C',str(path),'rev-parse','HEAD']).strip()
                    remote=manager.run(['git','-C',str(path),'remote','get-url','origin']).strip()
                    dirty=manager.run(['git','-C',str(path),'status','--porcelain','--untracked-files=all','--ignored']).strip()
                    owned=commit==path.name.rsplit('-',1)[1] and remote=='https://github.com/a-baader/wg-dual-easy.git' and not dirty
                elif kind=='snapshot' and path.is_dir() and re.fullmatch(r'[a-f0-9]{64}',path.name):
                    owned=snapshot_owned(path,manager.system_root)
                elif kind=='release' and path.is_file() and re.fullmatch(r'v\d+\.\d+\.\d+(?:-rc\.\d+)?\.json',path.name):
                    data=json.loads(path.read_text())
                    owned=path.stem=='v'+data['version'] and set(data['images'])=={'awg','wg'} and all(
                        re.fullmatch(r'ghcr\.io/a-baader/wg-dual-easy-'+name+r'@sha256:[a-f0-9]{64}',reference)
                        for name,reference in data['images'].items())
            except (ManagerError,OSError,ValueError,KeyError,TypeError):owned=False
            metadata=path.stat()
            items.append({'path':str(path),'kind':kind,'bytes':size(path),'age_days':max(0,int((time.time()-metadata.st_mtime)/86400)),
                          'mtime':metadata.st_mtime,'device':metadata.st_dev,'inode':metadata.st_ino,
                          'owned':owned,'protected':path.resolve() in protected})
    ancestor=manager.root
    while not ancestor.exists():ancestor=ancestor.parent
    usage=shutil.disk_usage(ancestor)
    warnings=[]
    if usage.free<256*1024*1024 or usage.free/max(usage.total,1)<.1:warnings.append('Мало свободного места; проверьте retention перед созданием backup или обновлением')
    return {'items':items,'total_bytes':sum(item['bytes'] for item in items),'free_bytes':usage.free,'warnings':warnings}


def execute(manager,args):
    report=inspect(manager)
    if args.action=='status':return report
    # Владение всей установкой требуется и после обычного uninstall.
    if not manager.config(False) and not any(item['kind']=='backup' and item['owned'] for item in report['items']):
        raise ManagerError('Нет подтверждения владения ресурсами хранения',73)
    selected=[]
    for kind in ('backup','source','snapshot','release'):
        keep=args.keep_backups if kind=='backup' else args.keep_sources
        candidates=sorted([item for item in report['items'] if item['kind']==kind and item['owned']],key=lambda item:item['mtime'],reverse=True)
        selected.extend(item for item in candidates[keep:] if not item['protected'] and item['age_days']>=args.older_than_days)
    if args.dry_run:return {**report,'dry_run':True,'remove':[item['path'] for item in selected]}
    # Повторная проверка состава и identity перед удалением; нет glob/rm -rf.
    current={item['path']:item for item in inspect(manager)['items']}
    for item in selected:
        now=current.get(item['path'])
        if not now or not now['owned'] or now['protected'] or any(now[key]!=item[key] for key in ('device','inode','mtime')):
            raise ManagerError('Ресурс хранения изменился; очистка остановлена',73)
    for item in selected:
        path=Path(item['path']);manager.install_stage('Удаление старого ресурса: '+str(path))
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()
    return {'removed':[item['path'] for item in selected],'reclaimed_bytes':sum(item['bytes'] for item in selected),
            'active_sources_preserved':True,'automatic_cleanup':False}

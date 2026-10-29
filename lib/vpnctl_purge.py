"""Полное удаление ресурсов приложения; исходники и чужие ресурсы сохраняются."""
import copy
import json
from pathlib import Path
import re
import shutil
from types import SimpleNamespace

import vpnctl_boot
from vpnctl_backup import verify_archive
from vpnctl_common import ManagerError

# Это приватные каталоги manager, а не произвольные пути из конфигурации.
DIRECTORIES=('backups','bootstrap','releases')
SOURCES={'https://github.com/a-baader/wg-dual-easy','https://github.com/a-baader/awg-easy-3'}
SHARED_ROOTS={Path(path) for path in ['/', '/etc', '/var', '/var/lib', '/var/backups', '/usr', '/usr/local', '/home', '/root', '/tmp', '/run']}


def check_paths(manager,config=None):
    if manager.root in SHARED_ROOTS or manager.root.resolve()!=manager.root or manager.root==manager.project or any(manager.root/name==manager.project or manager.root/name in manager.project.parents for name in DIRECTORIES):
        raise ManagerError('Небезопасный каталог manager для purge',73)
    for name in (*DIRECTORIES,'operations.jsonl','compose.json','transaction.json',manager.path.name):
        path=manager.root/name
        if path.is_symlink():raise ManagerError('Purge остановлен: обнаружена символьная ссылка: '+str(path),73)
        if path.exists() and name not in DIRECTORIES and not path.is_file():
            raise ManagerError('Purge остановлен: ожидался обычный файл: '+str(path),73)
        if path.exists() and name in DIRECTORIES and not path.is_dir():
            raise ManagerError('Purge остановлен: ожидался каталог: '+str(path),73)
    if manager.path.name in (*DIRECTORIES,'operations.jsonl','compose.json','transaction.json'):
        raise ManagerError('Имя конфигурации пересекается со служебными файлами purge',73)
    for item in (config or {}).get('components',{}).values():
        path=Path(item['data_directory'])
        if path.resolve()!=path or path==manager.project or path in manager.project.parents or path==manager.root or path in manager.root.parents:
            raise ManagerError('Небезопасный каталог данных для purge',73)
        for name in DIRECTORIES:
            internal=manager.root/name
            if path==internal or path in internal.parents or internal in path.parents:
                raise ManagerError('Каталог данных пересекается со служебным каталогом purge',73)


def image_candidates(manager,configs):
    images={item['image'] for config in configs for item in config.get('components',{}).values()}
    for source in sorted(SOURCES):
        raw=manager.run(['docker','image','ls','--quiet','--no-trunc','--filter','label=org.opencontainers.image.source='+source])
        images.update(raw.split())
    return sorted(image for image in images if re.fullmatch(r'sha256:[a-f0-9]{64}',image))


def purge(manager,args):
    manager.removal_stage('Проверка установки, внутренних backup и принадлежности ресурсов')
    config=manager.config(False)
    check_paths(manager,config)
    result={'purged':True,'sources_preserved':True,'backups_preserved':False,'forwarding_values_preserved':True}
    if not config and not manager.root.exists():return {**result,'purged':not args.dry_run,'dry_run':args.dry_run,'unchanged':True,'removed':[],'images_removed':[],'images_preserved':[]}
    configs=[config] if config else []
    # После uninstall восстановление доступно из внутренних архивов: они также
    # позволяют определить прежние образы и hashes установленных системных файлов.
    for path in sorted((manager.root/'backups').glob('*.tar.gz')):
        if path.is_symlink():continue
        try:configs.append(verify_archive(path)['manager_config'])
        except ManagerError:pass  # Повреждённый внутренний backup тоже подлежит удалению.
    if not configs:
        raise ManagerError('Владение каталогом manager не подтверждено: нет конфигурации или проверяемого внутреннего backup. Файлы сохранены',73)
    if config:manager.owned(config)
    images=image_candidates(manager,configs)
    if args.dry_run:
        return {**result,'purged':False,'dry_run':True,'remove':list((config or {}).get('components',{})),
                'remove_internal_directories':[str(manager.root/name) for name in DIRECTORIES],
                'image_candidates':images,'installed_command_removal_requested':True}
    if config:
        return manager.uninstall(SimpleNamespace(component='all',dry_run=False,purge=True,purge_images=images))
    previous=copy.deepcopy(configs[-1]) if configs else {'components':{}}
    previous['components']={}
    journal={'operation':'uninstall','phase':'committed','previous':previous,'candidate':{'components':{}},
             'selected':[],'purge':True,'purge_images':images}
    manager.save_journal(journal)
    return manager.finish_uninstall(journal)


def remove_images(manager,images):
    removed=[];preserved=[]
    for image in images:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ManagerError('Некорректный образ в журнале purge',65)
        raw=manager.run(['docker','image','inspect',image],check=False)
        if not raw:continue
        info=json.loads(raw)[0];settings=info.get('Config') or {}
        if info.get('Id')!=image or (settings.get('Labels') or {}).get('org.opencontainers.image.source') not in SOURCES:
            preserved.append({'image':image,'reason':'принадлежность проекту не подтверждена'});continue
        if manager.run(['docker','ps','-aq','--filter','ancestor='+image]).strip():
            preserved.append({'image':image,'reason':'используется другим контейнером'});continue
        references=sorted(set((info.get('RepoTags') or [])+(info.get('RepoDigests') or [])))
        own_reference=r'(?:(?:ghcr\.io/a-baader/(?:wg-dual-easy|awg-easy-3)-(?:awg|wg)|wg-dual-easy-(?:awg|wg))[:@].+|vpn-easy-(?:awg|wg)(?::dual-.+|@sha256:[a-f0-9]{64}))'
        if any(not re.fullmatch(own_reference,reference) for reference in references):
            preserved.append({'image':image,'reason':'имеет ссылки вне namespace приложения'});continue
        failed=False
        # Удаляем только собственные ссылки, без --force и глобального prune.
        for reference in references:
            if manager.run(['docker','image','inspect',reference],check=False) is None:continue
            if manager.run(['docker','image','rm',reference],check=False) is None:failed=True;break
        if not failed and manager.run(['docker','image','inspect',image],check=False):
            failed=manager.run(['docker','image','rm',image],check=False) is None
        if failed:preserved.append({'image':image,'reason':'Docker отказал в удалении без --force'})
        else:removed.append(image)
    return removed,preserved


def finish(manager,journal):
    check_paths(manager)
    manager.removal_stage('Удаление неиспользуемых образов проекта')
    removed,preserved=remove_images(manager,journal.get('purge_images',[]))
    manager.removal_stage('Удаление внутренних backup, первых профилей и служебных данных')
    for name in DIRECTORIES:
        path=manager.root/name
        if path.exists():shutil.rmtree(path)
    (manager.root/'operations.jsonl').unlink(missing_ok=True)
    manager.journal.unlink(missing_ok=True)
    # Неизвестные файлы в каталоге manager сохраняются.
    if manager.root.exists() and not any(manager.root.iterdir()):manager.root.rmdir()
    command=manager.system_root/'usr/local/sbin/vpnctl'
    known_files={**manager.system_files(),**vpnctl_boot.files(manager.system_root,'systemd'),**vpnctl_boot.files(manager.system_root,'openrc')}
    preserved_files=[str(path) for path in known_files if path.exists() or path.is_symlink()]
    if manager.root.exists():preserved_files.extend(str(path) for path in manager.root.iterdir())
    return {'purged':True,'preserved_files':sorted(preserved_files),'removed':journal['selected'],'sources_preserved':True,'backups_preserved':False,
            'forwarding_values_preserved':True,'vpnctl_removed':not command.exists() and not command.is_symlink(),
            'images_removed':removed,'images_preserved':preserved}

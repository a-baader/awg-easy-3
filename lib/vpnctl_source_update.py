"""Обновление CLI и контейнеров из одного проверенного стабильного релиза."""
from contextlib import contextmanager
import copy
import json
import re
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
from types import SimpleNamespace

import vpnctl_boot
from vpnctl_common import ManagerError,atomic_json,durable_replace


def stage_source(manager,release):
    sources=manager.root/'sources'
    if sources.is_symlink() or sources.resolve()!=sources:
        raise ManagerError('Небезопасный каталог исходников релизов',73)
    sources.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=sources/('v'+release['version']+'-'+release['source_commit'])
    def verify(path):
        if path.is_symlink():raise ManagerError('Каталог релиза является символьной ссылкой',73)
        revision=manager.run(['git','-C',str(path),'rev-parse','HEAD']).strip()
        if revision!=release['source_commit']:raise ManagerError('Commit исходников не совпадает с манифестом релиза',65)
        if manager.run(['git','-C',str(path),'status','--porcelain','--untracked-files=all']).strip():
            raise ManagerError('Исходники кешированного релиза изменены; файлы сохранены',73)
        if (path/'VERSION').read_text().strip()!=release['version'] or json.loads((path/'src/package.json').read_text()).get('version')!=release['version']:
            raise ManagerError('Версия исходников не совпадает с манифестом релиза',65)
        for name in ['vpnctl','awg-easy-3','lib/vpnctl_manager.py']:
            if not (path/name).is_file():raise ManagerError('В исходниках релиза отсутствует необходимый файл: '+name,65)
    if target.exists():verify(target);return target
    temporary=Path(tempfile.mkdtemp(prefix='.stage-',dir=sources))
    try:
        manager.run(['git','clone','--depth','1','--branch','v'+release['version'],'--',
                     'https://github.com/'+vpnctl_boot.REPOSITORY+'.git',str(temporary)])
        verify(temporary)
        durable_replace(temporary,target)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return target


@contextmanager
def release_namespace(project):
    # Новый manager выполняется в том же процессе: PID и обе блокировки
    # сохраняются даже при прерывании обновления исходников.
    names={path.stem for path in (project/'lib').glob('*.py')}
    saved={name:module for name,module in list(sys.modules.items()) if name in names}
    old_path=list(sys.path)
    for name in names:sys.modules.pop(name,None)
    sys.path.insert(0,str(project/'lib'))
    try:
        namespace=runpy.run_path(str(project/'lib/vpnctl_manager.py'),run_name='vpnctl_release_manager')
        if namespace.get('UPDATE_API_VERSION')!=1:raise ManagerError('Версия API обновления исходников не поддерживается',65)
        yield namespace
    finally:
        for name in names:sys.modules.pop(name,None)
        sys.modules.update(saved);sys.path[:]=old_path


def new_manager(namespace,project,manager):
    return namespace['Manager'](project,manager.path,system_root=manager.system_root)


def current_source_commit(manager,config):
    if config.get('source_commit'):return config['source_commit']
    # Первая установка из чистого стабильного Git-тега уже использует точные
    # исходники релиза и не должна повторно перезапускать те же контейнеры.
    revision=manager.run(['git','-C',str(manager.project),'rev-parse','HEAD'],check=False)
    if not revision or not re.fullmatch(r'[a-f0-9]{40}',revision.strip()):return None
    dirty=manager.run(['git','-C',str(manager.project),'status','--porcelain','--untracked-files=all'],check=False)
    return revision.strip() if dirty is not None and not dirty.strip() else None


def update(manager,args):
    config=manager.config();selected=manager.selected(config,args.component)
    manager.owned(config);manager.check_system_paths(config)
    try:release=vpnctl_boot.fetch_release()
    except Exception as error:
        raise ManagerError('Не удалось проверить стабильный релиз; установленная версия сохранена',69) from error
    if release is None:return {'updated':False,'reason':'стабильный релиз ещё не опубликован'}
    version=release['version']
    versions=[(manager.project/'VERSION').read_text().strip()]+[item.get('version',config['version']) for item in config['components'].values()]
    if any(vpnctl_boot.stable_version(value)>vpnctl_boot.stable_version(version) for value in versions):
        return {'updated':False,'reason':'стабильный релиз старше установленной версии; понижение не выполняется','version':version}
    changed=[name for name in selected if config['components'][name].get('version',config['version'])!=version
             or config['components'][name].get('image_ref')!=release['images'][name]]
    source_changed=current_source_commit(manager,config)!=release['source_commit']
    if not changed and not source_changed:return {'updated':False,'reason':'установлен актуальный стабильный релиз','version':version}
    if args.dry_run:
        return {'dry_run':True,'version':version,'source_commit':release['source_commit'],
                'update_sources':source_changed,'components':changed or selected,'images':{name:release['images'][name] for name in (changed or selected)}}
    # До начала обычной update-транзакции меняется только кеш исходников.
    # Recover этой фазы оставляет работающие сервисы и их состояние без изменений.
    journal={'operation':'source-update','phase':'prepared','previous':copy.deepcopy(config),
             'candidate':copy.deepcopy(config),'selected':[]}
    manager.save_journal(journal)
    try:
        project=stage_source(manager,release)
        manifest=manager.root/'releases'/('v'+version+'.json');atomic_json(manifest,release)
        with release_namespace(project) as namespace:
            target=new_manager(namespace,project,manager)
            result=target.update(SimpleNamespace(component=args.component,manifest=str(manifest),dry_run=False,
                                                 source_commit=release['source_commit']))
        return {**result,'source_commit':release['source_commit'],'sources_directory':str(project),'previous_sources_preserved':True}
    except Exception as error:
        if manager.journal.exists() and json.loads(manager.journal.read_text()).get('operation')=='source-update':
            manager.journal.unlink()
        if isinstance(error,ManagerError):raise
        raise ManagerError('Обновление стабильного релиза не завершено; при наличии журнала выполните vpnctl recover',getattr(error,'code',74)) from error

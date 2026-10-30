"""Защищённый снимок исходников для последующего запуска установленного CLI."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile

from vpnctl_common import ManagerError, durable_replace, atomic_json, atomic_write

IGNORED = {'.git', 'node_modules', '__pycache__', 'data', 'data-wg', '.venv', '.pytest_cache'}
SOURCE_DIRECTORIES={'lib','src','scripts','docs','nix','.github','assets','legacy'}
SOURCE_FILES={'VERSION','STATE_VERSION','LICENSE','Dockerfile','docker-compose.yml','docker-compose.dual.yml',
              'vpnctl','install.sh','awg-easy-3','README.md','README.ru.md','NOTICE','docker-compose.build.yml',
              '.dockerignore','.gitignore','.gitattributes','.editorconfig'}


def trusted(path, boundary):
    path, boundary = Path(path), Path(boundary)
    if path != boundary and boundary not in path.parents:
        raise ManagerError('Исходники CLI находятся вне управляемого каталога', 73)
    for current in [path, *path.parents]:
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise ManagerError('Небезопасный владелец или права исходников CLI: ' + str(current), 73)
        if current == boundary:
            break


def install(manager):
    project = manager.project
    destination = manager.system_root / 'usr/local/lib/wg-dual-easy/sources'
    if project == destination or project in destination.parents:
        raise ManagerError('Исходники CLI пересекаются с каталогом управляемых снимков', 73)
    def make_directory(path, boundary):
        if not boundary.exists():
            boundary.mkdir(mode=0o755)
        trusted(boundary, boundary)
        for current in reversed([path, *path.parents]):
            if boundary not in current.parents:
                continue
            if not current.exists():
                current.mkdir(mode=0o755)
            trusted(current, boundary)
    make_directory(destination, manager.system_root)
    excluded=set()
    if hasattr(manager,'root'):excluded.add(manager.root.resolve())
    if hasattr(manager,'config'):
        excluded.update(Path(item['data_directory']).resolve() for item in (manager.config(False) or {}).get('components',{}).values())
    contents = {}
    for directory, folders, files in os.walk(project, followlinks=False):
        relative = Path(directory).relative_to(project)
        folders[:] = sorted(name for name in folders if name not in IGNORED and not name.startswith('.env')
                            and (relative!=Path('.') or name in SOURCE_DIRECTORIES)
                            and (Path(directory)/name).resolve() not in excluded)
        for name in folders:
            if (Path(directory) / name).is_symlink():
                raise ManagerError('Символьные ссылки в исходниках CLI не поддерживаются', 73)
        for name in sorted(files):
            if relative==Path('.') and name not in SOURCE_FILES:continue
            if name=='_vpnctl_snapshot.json' or name.startswith('.env') or name.endswith(('.pyc', '.key', '.pem')):
                continue
            source = Path(directory) / name
            if source.is_symlink() or not source.is_file():
                raise ManagerError('Небезопасный объект в исходниках CLI', 73)
            contents[relative / name] = (source.read_bytes(), 0o755 if source.stat().st_mode & 0o111 else 0o644)
    digest = hashlib.sha256()
    for name, (value, mode) in sorted(contents.items()):
        digest.update(str(name).encode() + b'\0' + str(mode).encode() + b'\0' + hashlib.sha256(value).digest())
    target = destination / digest.hexdigest()
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=destination))
        try:
            for name, (value, mode) in contents.items():
                path = temporary / name
                make_directory(path.parent, temporary)
                atomic_write(path,value,mode)
            temporary.chmod(0o755)
            atomic_json(temporary/'_vpnctl_snapshot.json',{'format':1,'files':{
                str(name):{'sha256':hashlib.sha256(value).hexdigest(),'mode':mode} for name,(value,mode) in contents.items()}})
            # До переключения launcher синхронизируется и дерево каталогов.
            for directory in sorted([temporary,*[path for path in temporary.rglob('*') if path.is_dir()]],key=lambda path:len(path.parts),reverse=True):
                descriptor=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
            durable_replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    trusted(target, manager.system_root)
    for name, (value, _mode) in contents.items():
        path = target / name
        trusted(path, manager.system_root)
        if path.read_bytes() != value:
            raise ManagerError('Управляемые исходники CLI изменены; снимок сохранён', 73)
    return target

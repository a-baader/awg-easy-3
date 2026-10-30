"""Проверяемые архивы состояния; lifecycle и блокировка принадлежат manager."""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
from contextlib import contextmanager

from vpnctl_common import ManagerError, warn_storage_space

MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_FILES = 10000

def create_archive(config, selected, output):
    output = Path(output).absolute()
    if output.exists(): raise ManagerError('Файл резервной копии уже существует', 73)
    files = {}; total = 0
    for name in selected:
        source = Path(config['components'][name]['data_directory'])
        if source.is_symlink() or not (source/'state.json').is_file(): raise ManagerError('Не найдено корректное состояние компонента', 65)
        for item in source.rglob('*'):
            if item.is_symlink() or not (item.is_dir() or item.is_file()): raise ManagerError('Неподдерживаемый объект в каталоге состояния', 65)
            if item.is_dir(): continue
            size = item.stat().st_size; total += size
            if size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES: raise ManagerError('Превышен допустимый размер резервной копии', 65)
            archive_name = name + '/data/' + item.relative_to(source).as_posix()
            files[archive_name] = {'sha256': hashlib.sha256(item.read_bytes()).hexdigest(), 'size': size}
    schemas = {}
    for name in selected:
        try: schemas[name] = json.loads((Path(config['components'][name]['data_directory'])/'state.json').read_text())['version']
        except (OSError, ValueError, KeyError): raise ManagerError('Не удалось проверить схему состояния', 65) from None
    manifest = {'format_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'selected': list(selected), 'manager_config': config, 'schemas': schemas, 'files': files}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    warn_storage_space(output.parent,total)
    fd, temporary = tempfile.mkstemp(prefix='.vpnctl-backup-', dir=output.parent); os.close(fd)
    try:
        with tarfile.open(temporary, 'w:gz', dereference=True) as archive:
            data = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode()
            info = tarfile.TarInfo('manifest.json'); info.size=len(data); info.mode=0o600
            archive.addfile(info, io.BytesIO(data))
            for archive_name in files:
                name, _, relative = archive_name.partition('/data/')
                source = Path(config['components'][name]['data_directory'])/relative
                info = archive.gettarinfo(str(source), arcname=archive_name)
                info.mode=0o600; info.uid=0; info.gid=0; info.uname=''; info.gname=''
                with source.open('rb') as stream: archive.addfile(info, stream)
        os.chmod(temporary,0o600)
        verify_archive(temporary)
        with open(temporary,'rb') as stream:os.fsync(stream.fileno())
        # link не используется: workspace и некоторые VPS FS не поддерживают hard links.
        if output.exists(): raise ManagerError('Файл резервной копии появился во время записи',73)
        os.replace(temporary,output)
        fd=os.open(output.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(fd)
        finally:os.close(fd)
        return {'file':str(output),'components':list(selected),'files':len(files),'verified':True}
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

MAX_STATE = 16 * 1024 * 1024
MAX_COMPRESSED = MAX_TOTAL
MAX_VERIFY_SECONDS = 120


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        archive._vpnctl_headers = getattr(archive, '_vpnctl_headers', 0) + 1
        start = getattr(archive, '_vpnctl_start', time.monotonic())
        archive._vpnctl_start = start
        if archive._vpnctl_headers > 2 * MAX_FILES + 4 or time.monotonic() - start > MAX_VERIFY_SECONDS:
            raise ManagerError('Превышен бюджет заголовков или времени архива', 65)
        metadata = self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
                                 tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
        archive._vpnctl_metadata_depth = getattr(archive, '_vpnctl_metadata_depth', 0) + 1 if metadata else 0
        if self.type == tarfile.GNUTYPE_SPARSE or metadata and (self.size > 65536 or archive._vpnctl_metadata_depth > 16):
            raise ManagerError('Неподдерживаемые sparse или избыточные metadata архива', 65)
        return super()._proc_member(archive)

    def _proc_gnusparse_00(self, *args):
        raise ManagerError('Sparse-файлы в backup не поддерживаются', 65)
    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


@contextmanager
def bounded_archive(filename):
    with open(filename, 'rb') as source:
        if os.fstat(source.fileno()).st_size > MAX_COMPRESSED:
            raise ManagerError('Сжатый архив превышает допустимый размер', 65)
        with tarfile.open(fileobj=source, mode='r|gz', tarinfo=BoundedTarInfo) as archive:
            yield archive


def members(archive):
    names = set(); total = 0; started = time.monotonic()
    for item in archive:
        name = PurePosixPath(item.name)
        total += item.size
        if len(names) >= MAX_FILES + 1 or total > MAX_TOTAL or time.monotonic() - started > MAX_VERIFY_SECONDS:
            raise ManagerError('Архив превышает допустимый размер или время проверки', 65)
        if (item.name in names or name.is_absolute() or '..' in name.parts or name.as_posix() != item.name
                or '\\' in item.name or not item.isfile() or item.size < 0 or item.size > MAX_FILE
                or len(item.name) > 4096 or len(name.parts) > 64):
            raise ManagerError('Архив содержит небезопасные пути или объекты', 65)
        names.add(item.name)
        # В stream-режиме stdlib также кеширует TarInfo; сохранять весь кеш не нужно.
        archive.members.clear()
        yield item


def verify_archive(filename):
    try:
        actual = {}; captured = {}
        with bounded_archive(filename) as archive:
            for item in members(archive):
                capture = item.name == 'manifest.json' or item.name in ('awg/data/state.json', 'wg/data/state.json')
                if capture and item.size > (4 * 1024 * 1024 if item.name == 'manifest.json' else MAX_STATE):
                    raise ManagerError('Манифест или состояние слишком велики', 65)
                digest = hashlib.sha256(); chunks = []; started = time.monotonic()
                with archive.extractfile(item) as source:
                    while chunk := source.read(65536):
                        digest.update(chunk)
                        if capture: chunks.append(chunk)
                        if time.monotonic() - archive._vpnctl_start > MAX_VERIFY_SECONDS:
                            raise ManagerError('Превышено время проверки файла backup', 65)
                actual[item.name] = {'size': item.size, 'sha256': digest.hexdigest()}
                if capture: captured[item.name] = json.loads(b''.join(chunks))
        manifest = captured.get('manifest.json')
        if not isinstance(manifest, dict) or manifest.get('format_version') != 1 or not isinstance(manifest.get('files'), dict):
            raise ManagerError('Неизвестный формат резервной копии', 65)
        selected = manifest.get('selected')
        if not selected or len(set(selected)) != len(selected) or set(selected) - {'awg', 'wg'}:
            raise ManagerError('Некорректный список компонентов архива', 65)
        if set(actual) != {'manifest.json', *manifest['files']}:
            raise ManagerError('Состав архива не совпадает с манифестом', 65)
        for name, metadata in manifest['files'].items():
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[0] not in selected or parts[1] != 'data':
                raise ManagerError('Некорректный путь компонента архива', 65)
            if actual[name] != metadata:
                raise ManagerError('Контрольная сумма резервной копии не совпадает', 65)
        for name in selected:
            state = captured[name + '/data/state.json']
            expected = {'awg': {1, 2}, 'wg': {3}}[name]
            if state.get('version') not in expected or manifest.get('schemas', {}).get(name) != state.get('version'):
                raise ManagerError('Несовместимая схема состояния в архиве', 65)
            if name == 'wg' and state.get('server', {}).get('backend') != 'wireguard':
                raise ManagerError('Некорректный backend состояния WG', 65)
        return manifest
    except ManagerError:
        raise
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise ManagerError('Резервная копия повреждена или не поддерживается', 65) from None

def extract_component(filename, name, directory):
    manifest=verify_archive(filename)
    if name not in manifest['selected']: raise ManagerError('Компонент отсутствует в архиве',65)
    destination=Path(directory)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        with bounded_archive(filename) as archive:
            for member in members(archive):
                prefix=name+'/data/'
                if not member.name.startswith(prefix): continue
                relative=PurePosixPath(member.name[len(prefix):])
                target=destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with archive.extractfile(member) as source, open(target,'xb',opener=lambda path,flags:os.open(path,flags,0o600)) as output:
                    digest=hashlib.sha256();size=0;started=time.monotonic()
                    while chunk:=source.read(65536):
                        output.write(chunk);digest.update(chunk);size+=len(chunk)
                        if time.monotonic()-archive._vpnctl_start>MAX_VERIFY_SECONDS:raise ManagerError('Превышено время извлечения backup',65)
                    if {'size':size,'sha256':digest.hexdigest()}!=manifest['files'].get(member.name):
                        raise ManagerError('Архив изменился между проверкой и извлечением',65)
                    output.flush();os.fsync(output.fileno())
                target.chmod(0o600)
        directories=[destination,*[item for item in destination.rglob('*') if item.is_dir()]]
        for directory in sorted(directories,key=lambda value:len(value.parts),reverse=True):
            fd=os.open(directory,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try:os.fsync(fd)
            finally:os.close(fd)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination

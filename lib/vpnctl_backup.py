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

from vpnctl_common import ManagerError

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
        # link не используется: workspace и некоторые VPS FS не поддерживают hard links.
        if output.exists(): raise ManagerError('Файл резервной копии появился во время записи',73)
        os.replace(temporary,output)
        return {'file':str(output),'components':list(selected),'files':len(files),'verified':True}
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def verify_archive(filename):
    try:
        with tarfile.open(filename,'r:gz') as archive:
            members=archive.getmembers()
            if len(members)>MAX_FILES+1 or sum(item.size for item in members)>MAX_TOTAL:
                raise ManagerError('Архив превышает допустимый размер',65)
            names=set()
            for item in members:
                name=PurePosixPath(item.name)
                if item.name in names or name.is_absolute() or '..' in name.parts or name.as_posix()!=item.name or '\\' in item.name or not item.isfile() or item.size>MAX_FILE:
                    raise ManagerError('Архив содержит небезопасные пути или объекты',65)
                names.add(item.name)
            if 'manifest.json' not in names: raise ManagerError('Манифест резервной копии отсутствует',65)
            member=archive.getmember('manifest.json')
            if member.size>4*1024*1024: raise ManagerError('Манифест слишком велик',65)
            manifest=json.load(archive.extractfile(member))
            if manifest.get('format_version')!=1 or not isinstance(manifest.get('files'),dict): raise ManagerError('Неизвестный формат резервной копии',65)
            selected=manifest.get('selected')
            if not selected or len(set(selected))!=len(selected) or set(selected)-{'awg','wg'}: raise ManagerError('Некорректный список компонентов архива',65)
            if names!={'manifest.json',*manifest['files']}: raise ManagerError('Состав архива не совпадает с манифестом',65)
            for name, metadata in manifest['files'].items():
                parts=PurePosixPath(name).parts
                if len(parts)<3 or parts[0] not in selected or parts[1]!='data': raise ManagerError('Некорректный путь компонента архива',65)
                member=archive.getmember(name)
                if member.size!=metadata['size'] or hashlib.sha256(archive.extractfile(member).read()).hexdigest()!=metadata['sha256']:
                    raise ManagerError('Контрольная сумма резервной копии не совпадает',65)
            for name in selected:
                state=json.load(archive.extractfile(name+'/data/state.json'))
                expected={'awg':{1,2},'wg':{3}}[name]
                if state.get('version') not in expected or manifest.get('schemas',{}).get(name)!=state.get('version'):
                    raise ManagerError('Несовместимая схема состояния в архиве',65)
                if name=='wg' and state.get('server',{}).get('backend')!='wireguard': raise ManagerError('Некорректный backend состояния WG',65)
            return manifest
    except ManagerError: raise
    except (OSError, tarfile.TarError, ValueError, KeyError, TypeError):
        raise ManagerError('Резервная копия повреждена или не поддерживается',65) from None

def extract_component(filename, name, directory):
    manifest=verify_archive(filename)
    if name not in manifest['selected']: raise ManagerError('Компонент отсутствует в архиве',65)
    destination=Path(directory)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        with tarfile.open(filename,'r:gz') as archive:
            for member in archive.getmembers():
                prefix=name+'/data/'
                if not member.name.startswith(prefix): continue
                relative=PurePosixPath(member.name[len(prefix):])
                target=destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with archive.extractfile(member) as source, target.open('xb') as output: shutil.copyfileobj(source,output)
                target.chmod(0o600)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination

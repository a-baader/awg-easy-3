"""Учёт временных копий данных: намерение записывается до переименования."""
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from vpnctl_common import ManagerError, durable_replace


def retire(manager, journal, directory, prefix):
    directory = Path(directory)
    if not directory.exists() and not directory.is_symlink():
        return
    if directory.resolve() != directory or not directory.is_dir():
        raise ManagerError('Небезопасный каталог временной копии данных', 73)
    metadata = directory.stat()
    target = directory.parent / (prefix + uuid.uuid4().hex)
    entry = {'path': str(target), 'source': str(directory),
             'device': metadata.st_dev, 'inode': metadata.st_ino}
    journal.setdefault('retired_data', []).append(entry)
    manager.save_journal(journal)
    # При аварии до rename записи соответствует отсутствующий target; после —
    # тот же inode. Совпадение одного имени не даёт права удалять каталог.
    durable_replace(directory, target)


def cleanup(manager, journal):
    allowed = {Path(item['data_directory'])
               for config in (journal.get('previous'), journal.get('candidate')) if config
               for item in config.get('components', {}).values()}
    entries = journal.get('retired_data', [])
    # Проверяем весь список до первого удаления; чужие или подменённые пути
    # сохраняются вместе с журналом для разбирательства администратором.
    paths = []
    for entry in entries:
        path, source = Path(entry['path']), Path(entry['source'])
        if (source not in allowed or path.parent != source.parent
                or not re.fullmatch(r'\.(?:before-restore-|failed-(?:(?:awg|wg)-)?)[a-f0-9]{32}', path.name)
                or path.resolve() != path or path in allowed
                or path == manager.root or path in manager.root.parents
                or path == manager.project or path in manager.project.parents):
            raise ManagerError('Не подтверждено владение временной копией данных: ' + str(path), 73)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (entry['device'], entry['inode'])):
            raise ManagerError('Временная копия данных была подменена: ' + str(path), 73)
        paths.append(path)
    for path in paths:
        shutil.rmtree(path)
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

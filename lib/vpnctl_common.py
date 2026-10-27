"""Общие безопасные операции host manager, без сторонних Python-зависимостей."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile

class ManagerError(RuntimeError):
    def __init__(self, message, code=1):
        super().__init__(message); self.code = code

def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            os.fchmod(target.fileno(),mode)
            target.write(data.encode() if isinstance(data, str) else data)
            target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path)
        directory_fd=os.open(path.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def atomic_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def durable_replace(source,target):
    os.replace(source,target)
    for parent in {Path(source).parent,Path(target).parent}:
        fd=os.open(parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:os.fsync(fd)
        finally:os.close(fd)

def execute(args, *, input=None, check=True, env=None, merge_stderr=False):
    try:
        result = subprocess.run([str(v) for v in args], input=input, stdout=subprocess.PIPE, stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, text=True, timeout=900, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise ManagerError('Не удалось выполнить команду: ' + str(args[0]), 69) from None
    if result.returncode:
        if not check: return None
        # stderr и argv могут содержать данные конфигурации; они не журналируются.
        raise ManagerError('Команда завершилась с ошибкой: ' + str(args[0]) + ' (код ' + str(result.returncode) + ')')
    return result.stdout

@contextmanager
def global_lock(path='/run/lock/vpn-easy-dual.lock', legacy='/run/lock/awg-easy-3-update.lock', recovery_journal=None):
    lock = Path(path); lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired_legacy = False
    owner_pid=str(os.getpid())
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ManagerError('Другая операция vpnctl уже выполняется', 73) from None
        old = Path(legacy)
        if old.exists() and recovery_journal is not None:
            try:
                journal=json.loads(Path(recovery_journal).read_text())
                pid=int((old/'pid').read_text().strip())
                if pid<=1 or pid!=journal.get('lock_owner_pid'): raise ValueError()
                try: os.kill(pid,0)
                except ProcessLookupError:
                    # Удаляется только известная блокировка погибшего владельца транзакции.
                    stale=old.with_name(old.name+'.stale-'+str(os.getpid()))
                    os.rename(old,stale)
                    (stale/'pid').unlink();stale.rmdir()
            except (OSError,ValueError,KeyError): pass
        try:
            old.mkdir(mode=0o700)
            acquired_legacy = True
            try: atomic_write(old/'pid', str(os.getpid())+'\n')
            except BaseException:
                if not (old/'pid').exists():old.rmdir()
                raise
        except FileExistsError:
            parent=str(os.getppid())
            try: own=old.joinpath('pid').read_text().strip()==parent
            except OSError: own=False
            if own and os.environ.get('VPNCTL_BOOTSTRAP_LOCK')==parent:
                acquired_legacy=True;owner_pid=parent
            else: raise ManagerError('Работает legacy updater или осталась его блокировка; проверьте процесс перед продолжением', 73) from None
        yield
    finally:
        if acquired_legacy:
            try:
                if (Path(legacy)/'pid').read_text().strip()==owner_pid:
                    (Path(legacy)/'pid').unlink(missing_ok=True)
                    Path(legacy).rmdir()
            except FileNotFoundError: pass
        os.close(fd)


def redact(value):
    value=re.sub(r'vpn://[^\s"\']+','[ПРОФИЛЬ СКРЫТ]',str(value),flags=re.I)
    value=re.sub(r'[A-Za-z0-9+/]{43}=', '[КЛЮЧ СКРЫТ]',value)
    value=re.sub(r'\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}', '[ХЕШ СКРЫТ]',value)
    value=re.sub(r'(awg_easy_3_session=)[^;\s]+',r'\1[СЕССИЯ СКРЫТА]',value)
    return re.sub(r'((?:privateKey|presharedKey|sessionSecret|passwordHash|password)\s*["\']?\s*[:=]\s*)["\']?[^\s,"\'}]+',r'\1[СЕКРЕТ СКРЫТ]',value,flags=re.I)

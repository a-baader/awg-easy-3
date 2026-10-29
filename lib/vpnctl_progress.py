"""Прогресс установки: понятные этапы без раскрытия ответов RPC и секретов."""
from functools import wraps
import sys
import threading
import time
from vpnctl_terminal import message as terminal_message


class InstallationProgress:
    def __init__(self,interval=10):
        self.interval=interval;self.message='';self.started=time.monotonic()
        self.lock=threading.Lock();self.stopped=threading.Event();self.thread=None
    def stage(self,message):
        with self.lock:
            self.message=message;self.started=time.monotonic()
            terminal_message('[Установка] '+message,'error' if message.startswith('Установка не завершена') else 'success' if message=='Установка завершена' else 'label',file=sys.stderr,flush=True)
    def heartbeat(self):
        with self.lock:
            if self.message:
                elapsed=int(time.monotonic()-self.started)
                terminal_message('[Установка] '+self.message+' — выполняется, прошло '+str(elapsed)+' с','label',file=sys.stderr,flush=True)
    def start(self):
        def repeat():
            while not self.stopped.wait(self.interval):self.heartbeat()
        self.thread=threading.Thread(target=repeat,name='vpnctl-install-progress',daemon=True);self.thread.start()
    def stop(self):
        self.stopped.set()
        if self.thread:self.thread.join()


def installation_progress(method):
    @wraps(method)
    def wrapped(manager,args):
        if getattr(manager,'_installation_progress',None) or getattr(args,'dry_run',False):return method(manager,args)
        progress=InstallationProgress();manager._installation_progress=progress;progress.start()
        try:
            result=method(manager,args)
        except BaseException:
            progress.stop();progress.stage('Установка не завершена; подробности указаны в сообщении об ошибке')
            raise
        else:
            progress.stop();progress.stage('Установка завершена')
            return result
        finally:del manager._installation_progress
    return wrapped

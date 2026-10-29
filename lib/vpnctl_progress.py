"""Прогресс операций CLI: ASCII-индикаторы и этапы без раскрытия секретов."""
from contextlib import contextmanager
from functools import wraps
import os
import sys
import threading
import time
from vpnctl_terminal import message as terminal_message,ProgressLine


class InstallationProgress:
    def __init__(self,interval=10):
        self.line=ProgressLine();self.frame=0;self.finished=False;self.interval=interval;self.message='';self.started=time.monotonic()
        self.lock=threading.Lock();self.stopped=threading.Event();self.thread=None
    def display(self,text,style='label',**kwargs):
        final=self.finished or self.stopped.is_set() or style=='error'
        self.line.write(text,style,final=final)
    def bar(self):
        if self.finished:return '['+'#'*20+'] 100%'
        position=self.frame%17;self.frame+=1
        return '['+'.'*position+'###'+'.'*(17-position)+']'
    def stage(self,message):
        with self.lock:
            self.message=message;self.started=time.monotonic()
            self.finished=message=='Установка завершена'
            self.display('[Установка] '+message+' '+self.bar(),'error' if message.startswith('Установка не завершена') else 'success' if message=='Установка завершена' else 'label',file=sys.stderr,flush=True)
    def heartbeat(self):
        with self.lock:
            if self.message:
                elapsed=int(time.monotonic()-self.started)
                self.display('[Установка] '+self.message+' — выполняется, прошло '+str(elapsed)+' с '+self.bar(),'label',file=sys.stderr,flush=True)
    def start(self):
        def repeat():
            while not self.stopped.wait(self.interval):self.heartbeat()
        self.thread=threading.Thread(target=repeat,name='vpnctl-install-progress',daemon=True);self.thread.start()
    def stop(self):
        self.stopped.set()
        if self.thread:self.thread.join()
        self.line.finish()


def installation_progress(method):
    @wraps(method)
    def wrapped(manager,args):
        if getattr(manager,'_installation_progress',None) or getattr(args,'dry_run',False):return method(manager,args)
        progress=InstallationProgress(interval=0.2 if sys.stderr.isatty() and os.environ.get('TERM')!='dumb' else 10);manager._installation_progress=progress;progress.start()
        try:
            result=method(manager,args)
        except KeyboardInterrupt:
            progress.stop()
            raise
        except BaseException:
            progress.stop();progress.stage('Установка не завершена; подробности указаны в сообщении об ошибке')
            raise
        else:
            progress.stop();progress.stage('Установка завершена')
            return result
        finally:del manager._installation_progress
    return wrapped


class RemovalProgress(InstallationProgress):
    def stage(self,message):
        with self.lock:
            self.message=message;self.started=time.monotonic()
            style='error' if message.startswith('Удаление не завершено') else 'success' if message=='Удаление завершено' else 'label'
            self.finished=message=='Удаление завершено'
            self.display('[Удаление] '+message+' '+self.bar(),style,file=sys.stderr,flush=True)
    def heartbeat(self):
        with self.lock:
            if self.message:
                elapsed=int(time.monotonic()-self.started)
                self.display('[Удаление] '+self.message+' — выполняется, прошло '+str(elapsed)+' с '+self.bar(),'label',file=sys.stderr,flush=True)


def removal_progress(method):
    @wraps(method)
    def wrapped(manager,args):
        if getattr(manager,'_removal_progress',None) or getattr(args,'dry_run',False):return method(manager,args)
        progress=RemovalProgress(interval=0.2 if sys.stderr.isatty() and os.environ.get('TERM')!='dumb' else 10);manager._removal_progress=progress;progress.start()
        try:
            result=method(manager,args)
        except KeyboardInterrupt:
            progress.stop()
            raise
        except BaseException:
            progress.stop();progress.stage('Удаление не завершено; проверьте сообщение об ошибке и vpnctl recover')
            raise
        else:
            progress.stop();progress.stage('Изменения не требуются' if result.get('unchanged') else 'Удаление завершено')
            return result
        finally:del manager._removal_progress
    return wrapped


class CommandProgress(InstallationProgress):
    def __init__(self,title,interval=10):
        super().__init__(interval);self.title=title
    def stage(self,message):
        with self.lock:
            self.message=message;self.started=time.monotonic()
            self.display('['+self.title+'] '+message+' '+self.bar(),
                             'success' if self.finished else 'label',file=sys.stderr,flush=True)
    def heartbeat(self):
        with self.lock:
            elapsed=int(time.monotonic()-self.started)
            self.display('['+self.title+'] '+self.message+' '+self.bar()+' — прошло '+str(elapsed)+' с',
                             'label',file=sys.stderr,flush=True)


@contextmanager
def command_progress(args,manager=None):
    # Установка и удаление уже имеют подробные этапы; logs передаёт поток напрямую.
    if getattr(args,'dry_run',False) or args.command in ('install','reinstall','uninstall','purge','logs','help'):
        yield
        return
    from vpnctl_output import COMMANDS
    title=COMMANDS.get(args.command,'Смена пароля' if args.command=='reset-password' else args.command)
    if getattr(args,'action',None):title+=': '+args.action
    progress=CommandProgress(title,interval=0.2 if sys.stderr.isatty() and os.environ.get('TERM')!='dumb' else 10)
    if manager is not None:manager._command_progress=progress
    progress.stage('Выполняется');progress.start()
    try:yield
    except KeyboardInterrupt:
        progress.stop()
        raise
    except BaseException:
        progress.stop()
        terminal_message('['+title+'] Выполнение не завершено','error',file=sys.stderr,flush=True)
        raise
    else:
        progress.stop();progress.finished=True;progress.stage('Завершено')
    finally:
        if manager is not None:del manager._command_progress

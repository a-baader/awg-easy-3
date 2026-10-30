"""Прогресс операций CLI: ASCII-индикаторы и этапы без раскрытия секретов."""
from contextlib import contextmanager
from functools import wraps
import sys
import threading
import time
from vpnctl_terminal import message as terminal_message,ProgressLine


class InstallationProgress:
    title='Установка'
    completion_message='Установка завершена'
    failure_prefix='Установка не завершена'
    compact_heartbeat=False
    def __init__(self,interval=10):
        self.line=ProgressLine();self.frame=0;self.finished=False;self.interval=interval;self.message='';self.started=time.monotonic()
        self.lock=threading.Lock();self.stopped=threading.Event();self.thread=None
    def display(self,text,style='label',**kwargs):
        final=self.finished or self.stopped.is_set() or style=='error'
        self.line.write(text,style,final=final,transient=kwargs.get('transient',False))
    def bar(self):
        if self.finished:return '['+'#'*20+'] 100%'
        position=self.frame%17;self.frame+=1
        return '['+'.'*position+'###'+'.'*(17-position)+']'
    def stage(self,message):
        with self.lock:
            self.message=message;self.started=time.monotonic()
            if self.completion_message is not None:self.finished=message==self.completion_message
            style='error' if self.failure_prefix and message.startswith(self.failure_prefix) else 'success' if self.finished else 'label'
            self.display('['+self.title+'] '+message+' '+self.bar(),style,file=sys.stderr,flush=True)
    def heartbeat(self):
        with self.lock:
            if not self.message:return
            elapsed=int(time.monotonic()-self.started)
            suffix=' — прошло '+str(elapsed)+' с'
            text='['+self.title+'] '+self.message
            if self.compact_heartbeat:text+=' '+self.bar()+suffix
            else:text+=' — выполняется, прошло '+str(elapsed)+' с '+self.bar()
            self.display(text,'label',file=sys.stderr,flush=True,transient=True)
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
        progress=InstallationProgress(interval=0.2 if sys.stderr.isatty() else 10);manager._installation_progress=progress;progress.start()
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
    title='Удаление'
    completion_message='Удаление завершено'
    failure_prefix='Удаление не завершено'


def removal_progress(method):
    @wraps(method)
    def wrapped(manager,args):
        if getattr(manager,'_removal_progress',None) or getattr(args,'dry_run',False):return method(manager,args)
        progress=RemovalProgress(interval=0.2 if sys.stderr.isatty() else 10);manager._removal_progress=progress;progress.start()
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
    completion_message=None
    failure_prefix=None
    compact_heartbeat=True
    def __init__(self,title,interval=10):
        super().__init__(interval);self.title=title


@contextmanager
def command_progress(args,manager=None):
    # Установка и удаление уже имеют подробные этапы; logs передаёт поток напрямую.
    if getattr(args,'dry_run',False) or args.command in ('install','reinstall','uninstall','purge','logs','help'):
        yield
        return
    from vpnctl_output import COMMANDS
    title=COMMANDS.get(args.command,'Смена пароля' if args.command=='reset-password' else args.command)
    if getattr(args,'action',None):title+=': '+args.action
    progress=CommandProgress(title,interval=0.2 if sys.stderr.isatty() else 10)
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

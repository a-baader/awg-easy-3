"""Цвета только для читаемого вывода в интерактивный терминал."""
from contextlib import contextmanager
import threading
import os
import re
import sys

STYLES={'heading':'1;36','label':'36','success':'32','warning':'33','error':'31','prompt':'1;33'}


def color_enabled(stream=None):
    stream=sys.stdout if stream is None else stream
    return ('NO_COLOR' not in os.environ and os.environ.get('TERM')!='dumb'
            and bool(getattr(stream,'isatty',lambda:False)()))


def paint(text,style='label',stream=None):
    text=str(text)
    return '\033['+STYLES[style]+'m'+text+'\033[0m' if text and color_enabled(stream) else text


def message(text,style=None,**kwargs):
    stream=kwargs.get('file',sys.stdout)
    if style is None:
        if text.startswith(('Ошибка','Команда выполнена, но')):style='error'
        elif text.startswith(('Предупреждение','Операция прервана')):style='warning'
        else:style='prompt'
    with _line_lock:
        if _active_line is not None:_active_line.finish()
        print(paint(text,style,stream),**kwargs)


def help_text(text,stream):
    lines=[]
    for line in text.splitlines(keepends=True):
        if line.startswith(('Использование:','Команды:','Параметры:')):
            line=paint(line.rstrip('\n'),'heading',stream)+('\n' if line.endswith('\n') else '')
        elif line.startswith('  '):
            # Выделяется колонка команд/опций, ширину справки ANSI-коды не меняют.
            line=re.sub(r'^(  )(\S.*?)(?= {2,}|\n|$)',
                        lambda match:match[1]+paint(match[2],'label',stream),line)
        lines.append(line)
    return ''.join(lines)


_line_lock=threading.RLock()
_active_line=None
_last_line=None


class ProgressLine:
    """Одна строка stderr в TTY; построчный журнал при перенаправлении."""
    def __init__(self):
        self.stream=None;self.width=0;self.paused=False
    def write(self,text,style='label',final=False):
        global _active_line,_last_line
        with _line_lock:
            if self.paused:return
            _last_line=self
            stream=sys.stderr
            if not stream.isatty() or os.environ.get('TERM')=='dumb':
                self.finish()
                print(paint(text,style,stream),file=stream,flush=True)
                return
            if _active_line is not None and _active_line is not self:_active_line.finish()
            if self.stream is not None and self.stream is not stream:self.finish()
            try:columns=os.get_terminal_size(stream.fileno()).columns or 80
            except (OSError,ValueError):columns=80
            # Полоса остаётся видимой даже в узком окне терминала.
            bar=re.search(r'\[[.#]+\](?: 100%)?',text)
            if bar:text=bar[0]+' '+(text[:bar.start()]+text[bar.end():]).strip()
            text=text[:max(1,columns-1)]
            padding=' '*max(0,min(self.width,max(1,columns-1))-len(text))
            stream.write('\r'+paint(text,style,stream)+padding)
            stream.flush();self.stream=stream;self.width=len(text);_active_line=self
            if final:self.finish()
    def finish(self):
        global _active_line
        with _line_lock:
            if self.stream is not None:
                self.stream.write('\n');self.stream.flush()
                self.stream=None;self.width=0
            if _active_line is self:_active_line=None


@contextmanager
def progress_prompt():
    with _line_lock:
        line=_active_line or _last_line
        if line is not None:
            line.finish();line.paused=True
    try:yield
    finally:
        if line is not None:
            with _line_lock:line.paused=False

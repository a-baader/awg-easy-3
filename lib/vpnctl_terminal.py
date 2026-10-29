"""Цвета только для читаемого вывода в интерактивный терминал."""
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

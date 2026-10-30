"""Проверка локальных Markdown-ссылок, включая сохранённые исторические отчёты."""
from pathlib import Path
import re
from urllib.parse import unquote

PROJECT=Path(__file__).resolve().parents[1]
errors=[];count=0
paths=[PROJECT/'README.md',PROJECT/'README.ru.md',*sorted((PROJECT/'docs').rglob('*.md'))]
for path in paths:
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',path.read_text()):
        target=target.strip().split(' "',1)[0].strip('<>')
        if not target or re.match(r'(?:[a-z]+:|#|/)',target,re.I):continue
        filename=unquote(target.split('#',1)[0])
        if not filename:continue
        count+=1
        if not (path.parent/filename).exists():errors.append(str(path.relative_to(PROJECT))+': '+target)
if errors:raise SystemExit('Не найдены цели ссылок:\n'+'\n'.join(errors))
print('Проверены локальные ссылки Markdown: '+str(count))

"""Текущие версии берутся из VERSION; исторические отчёты не переписываются."""
import argparse
from pathlib import Path
import re

PROJECT=Path(__file__).resolve().parents[1]


def synchronize(check=False):
    version=(PROJECT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.[1-9]\d*)?',version):raise ValueError('Некорректная VERSION')
    replacements={
        'README.md':[(r'(> Current release: \*\*)[^*]+',r'\g<1>'+version),(r'docs/releases/v[\d.]+(?:-rc\.\d+)?\.md','docs/releases/v'+version+'.md')],
        'README.ru.md':[(r'(> Текущий выпуск: \*\*)[^*]+',r'\g<1>'+version),(r'docs/releases/v[\d.]+(?:-rc\.\d+)?\.md','docs/releases/v'+version+'.md')],
        'docs/README.md':[(r'- \[Стабильный выпуск [^\]]+\]\(releases/v[^)]+\)','- [Стабильный выпуск '+version+'](releases/v'+version+'.md)')],
        'docs/INSTALL_DUAL.ru.md':[(r'(Стабильный выпуск — \*\*)[^*]+',r'\g<1>'+version)],
    }
    changed=[]
    for name,patterns in replacements.items():
        path=PROJECT/name;text=path.read_text();updated=text
        for pattern,replacement in patterns:updated=re.sub(pattern,replacement,updated)
        if updated!=text:
            changed.append(name)
            if not check:path.write_text(updated)
    if check and changed:raise SystemExit('Версии документации устарели: '+', '.join(changed))
    return changed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Синхронизировать текущие версии из VERSION')
    parser.add_argument('--check',action='store_true');args=parser.parse_args()
    synchronize(args.check);print('Версии текущей документации соответствуют VERSION')

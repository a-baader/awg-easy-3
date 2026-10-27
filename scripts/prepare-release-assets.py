"""Собирает проверяемый manifest релиза из digest обоих опубликованных образов."""
import argparse
import hashlib
import json
from pathlib import Path
import re

parser=argparse.ArgumentParser(description='Создать manifest и команды установки из digest AWG/WG')
parser.add_argument('--digests',required=True);parser.add_argument('--output',required=True)
parser.add_argument('--repository',required=True);parser.add_argument('--commit',required=True)
args=parser.parse_args()
project=Path(__file__).resolve().parents[1];version=(project/'VERSION').read_text().strip()
if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',args.repository) or not re.fullmatch(r'[a-f0-9]{40}',args.commit):raise SystemExit('Ошибка: некорректные metadata релиза')
records={}
for file in Path(args.digests).glob('*.json'):
    item=json.loads(file.read_text());backend=item.get('backend');digest=item.get('digest')
    if backend not in ('awg','wg') or backend in records or not re.fullmatch(r'sha256:[a-f0-9]{64}',digest or ''):raise SystemExit('Ошибка: некорректный digest образа')
    records[backend]='ghcr.io/'+args.repository.lower()+'-'+backend+'@'+digest
if set(records)!={'awg','wg'}:raise SystemExit('Ошибка: требуются digest обоих образов')
output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
manifest={'version':version,'images':records,'source_commit':args.commit,'platform':'linux/amd64'}
(output/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
command="sudo ./vpnctl install --host YOUR_PUBLIC_IP \\\n  --awg-image '"+records['awg']+"' \\\n  --wg-image '"+records['wg']+"'\n"
(output/'install-command.txt').write_text(command)
notes=(project/'docs/releases'/('v'+version+'.md')).read_text()
notes+='\n## Образы по неизменяемым digest\n\n'+''.join('- '+name.upper()+': `'+records[name]+'`\n' for name in ['awg','wg'])
notes+='\nПосле получения исходников этого тега выполните из каталога проекта:\n\n```sh\n'+command+'```\n'
(output/'notes.md').write_text(notes)
checks=[]
for name in ['release-manifest.json','install-command.txt']:
    checks.append(hashlib.sha256((output/name).read_bytes()).hexdigest()+'  '+name)
(output/'SHA256SUMS').write_text('\n'.join(checks)+'\n')
print('Manifest и инструкции созданы для '+version)

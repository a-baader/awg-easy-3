"""Read-only проверка выбора сервисов и сохранности legacy data path."""
import json
import os
from pathlib import Path
import subprocess

PROJECT=Path(__file__).resolve().parents[1]
environment={key:value for key,value in os.environ.items() if not key.startswith('AWG_')}


def config(*arguments):
    command=['docker','compose','--env-file','/dev/null','-f',str(PROJECT/'docker-compose.yml'),*arguments,'config','--format','json']
    return json.loads(subprocess.check_output(command,cwd=PROJECT,env=environment,text=True))


assert not config()['services'], 'Корневой Compose не должен запускать сервис без явного legacy-профиля'
legacy=config('--profile','legacy')['services']['awg-easy']
assert legacy['image']=='ghcr.io/alexsemenovru/awg-easy-3:0.1.6'
mount=next(item for item in legacy['volumes'] if item['target']=='/data')
assert mount['source']==str(PROJECT/'data'), 'Legacy не должен переключаться на новый каталог данных'
print('PASS: default Compose пуст; явный legacy сохраняет исходные образ и data path')

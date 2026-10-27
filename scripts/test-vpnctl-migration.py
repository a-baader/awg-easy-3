"""Реальный manager и два образа в одноразовом общем network namespace.
Все host network/sysctl/nft/ip операции перенаправляются в anchor.
Системные файлы создаются только под временным system_root.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from unittest import mock
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import Manager,build_parser
from vpnctl_common import execute,atomic_json,ManagerError
prefix='vpnctl-real-'+uuid.uuid4().hex[:10]
anchor=prefix+'-host'
root=Path(tempfile.mkdtemp(prefix=prefix+'-'));root.chmod(0o700)
images={'awg':os.environ.get('TEST_AWG_IMAGE','vpn-easy-awg:dual-stage5'),'wg':os.environ.get('TEST_WG_IMAGE','vpn-easy-wg:dual-stage5')}
containers=[]
class IsolatedManager(Manager):
    def __init__(self):
        super().__init__(PROJECT,root/'manager/config.json',runner=self.isolated_run,system_root=root/'system')
    def isolated_run(self,args,**kwargs):
        args=[prefix+'-awg' if str(a)=='awg-easy-3' else str(a) for a in args]
        if args[0]=='sysctl' and args[1]=='-w':
            key,value=args[2].split('=',1)
            assert execute(['docker','exec',anchor,'sysctl','-n',key]).strip()==value
            return key+' = '+value+'\n'
        if args[0] in ('ip','ss','nft','sysctl'):args=['docker','exec',anchor,*args]
        elif args[:2]==['docker','run']:
            args=copy.copy(args)
            for i,arg in enumerate(args):
                if arg=='--network' and args[i+1]=='host':args[i+1]='container:'+anchor
        result=execute(args,**kwargs)
        if args[:2]==['docker','inspect'] and len(args)==3 and result:
            info=json.loads(result)
            for item in info:
                if item.get('HostConfig',{}).get('NetworkMode')=='container:'+execute(['docker','inspect','-f','{{.Id}}',anchor]).strip():item['HostConfig']['NetworkMode']='host'
                elif item.get('HostConfig',{}).get('NetworkMode')=='container:'+anchor:item['HostConfig']['NetworkMode']='host'
            return json.dumps(info)
        return result
    def inspect_container(self,name):
        return super().inspect_container(prefix+'-awg' if name=='awg-easy-3' else name)
    def legacy(self):
        value=super().legacy()
        if value:value['item']['container']=prefix+'-awg'
        return value
    def plan_install(self,args):
        value=super().plan_install(args)
        for name,item in value['components'].items():item['container']=prefix+'-'+name
        return value
    def write_compose(self,config):
        super().write_compose(config)
        value=json.loads(self.compose_path.read_text());value['name']=prefix
        for item in value['services'].values():
            item['network_mode']='container:'+anchor
            item['restart']='no'
            item['healthcheck']={'interval':'1s','timeout':'5s','start_period':'1s','retries':10}
        atomic_json(self.compose_path,value)

def passed(message):print('PASS: '+message,flush=True)
import time
import multiprocessing
import signal
from vpnctl_common import global_lock
original='ghcr.io/alexsemenovru/awg-easy-3@sha256:4a218ff42b401fd84a25c576090a42d52e204d8f3b4fd530ec352021580199ec'
helper=PROJECT/'scripts/test-awg-container.cjs'
legacy=root/'legacy';legacy.mkdir();data=legacy/'data';data.mkdir(mode=0o700)
old_manager=execute(['git','-C',str(PROJECT),'show','930dc6021024db9c8bbda7d92944aa9fef7baf3c:awg-easy-3'])
(legacy/'awg-easy-3').write_text(old_manager)
(legacy/'lib').mkdir()
# В disposable стенде фиксируется вызов disable; systemd/OpenRC хоста не вызываются.
(legacy/'lib/auto-update.sh').write_text('awg_easy_auto_update_disable() { touch "$project_dir/updater-disabled"; }\n')
manager=IsolatedManager()
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
      '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    execute(['docker','run','--rm','--network','container:'+anchor,'-v',str(data)+':/data','-v',str(helper)+':/test.cjs:ro',
      '--entrypoint','node',original,'/test.cjs','fixture',host])
    native_before=(data/'probe.conf').read_bytes();state_before=(data/'state.json').read_bytes()
    image_id=json.loads(execute(['docker','image','inspect',original]))[0]['Id']
    service={'image':image_id,'container_name':prefix+'-awg','network_mode':'container:'+anchor,'cap_add':['NET_ADMIN'],
      'devices':['/dev/net/tun:/dev/net/tun'],'volumes':[str(data)+':/data'],'read_only':True,
      # Оригинальный healthcheck 0.1.6 читает порт из environment.
      'environment':{'AWG_PANEL_PORT':'51821'},
      'tmpfs':['/run:mode=0700','/tmp:mode=1777'],'healthcheck':{'interval':'1s','timeout':'5s','start_period':'1s','retries':10}}
    atomic_json(legacy/'docker-compose.yml',{'name':prefix+'-old','services':{'awg-easy':service}})
    execute(['docker','compose','--project-directory',str(legacy),'-f',str(legacy/'docker-compose.yml'),'up','-d'])
    manager.health({'container':prefix+'-awg'})
    execute(['docker','run','-d','--name',prefix+'-client','--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
      '-v',str(data)+':/data:ro','-v',str(helper)+':/test.cjs:ro','--entrypoint','node',original,'/test.cjs','client','probe'])
    for _ in range(50):
        if 'Test client ready.' in execute(['docker','logs',prefix+'-client']):break
        time.sleep(.1)
    traffic=['docker','exec',prefix+'-client','node','/test.cjs','traffic','10.8.0.1','51821','allow']
    execute(traffic);passed('профиль создан оригинальным 0.1.6, клиент передаёт HTTP до миграции')
    parsed=manager.legacy();assert parsed['item']['udp_port']==54321
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'new'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    def interrupted_migration():
        def stop_before_snapshot(*_args,**_kwargs):os.kill(os.getpid(),signal.SIGKILL)
        with global_lock(root/'global.lock',root/'legacy.lock'):
            with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}),mock.patch('vpnctl_manager.create_archive',side_effect=stop_before_snapshot):
                IsolatedManager().install(args)
    process=multiprocessing.get_context('fork').Process(target=interrupted_migration)
    process.start();process.join(timeout=120)
    if process.is_alive():process.kill();process.join();raise AssertionError('Не достигнута точка прерывания миграции')
    assert process.exitcode==-signal.SIGKILL
    with global_lock(root/'global.lock',root/'legacy.lock',recovery_journal=manager.journal):
        manager.rollback(json.loads(manager.journal.read_text()))
    assert (data/'state.json').read_bytes()==state_before and not manager.journal.exists()
    connected=False
    for _ in range(18):
        try:execute(traffic);connected=True;break
        except ManagerError:time.sleep(.5)
    assert connected,'Старый клиент не переподключился после восстановления исходного сервера'
    passed('SIGKILL до миграционного snapshot: исходный контейнер и прежний клиент восстановлены')
    original_health=manager.health;failed=[False]
    def fail_wg_health(item):
        original_health(item)
        if item.get('backend')=='wireguard' and not failed[0]:
            failed[0]=True;raise ManagerError('Искусственный отказ WG после запуска')
    manager.health=fail_wg_health
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):
        try:manager.install(args)
        except ManagerError:pass
        else:raise AssertionError('Ожидался отказ миграции')
    manager.health=original_health
    assert failed[0] and manager.config(False) is None and not manager.journal.exists()
    assert (data/'state.json').read_bytes()==state_before
    assert manager.inspect_container(prefix+'-awg')['Image']==image_id
    connected=False
    for _ in range(18):
        try:execute(traffic);connected=True;break
        except ManagerError:time.sleep(.5)
    assert connected,'Клиент не передаёт HTTP после rollback миграции'
    passed('отказ нового WG: восстановлены исходные image/state AWG и HTTP старого клиента')
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
    cfg=manager.config()
    assert (data/'state.json').read_bytes()==state_before and (data/'probe.conf').read_bytes()==native_before
    assert cfg['components']['awg']['udp_port']==54321
    assert (legacy/'updater-disabled').is_file()
    backups=list((manager.root/'backups').glob('before-migration-*.tar.gz'));assert len(backups)==2
    from vpnctl_backup import verify_archive
    assert verify_archive(backups[0])['schemas']['awg']==1
    passed('миграция сохранила состояние, ключи, auth, ограничения, порт и .conf; snapshot проверен')
    connected=False
    for _ in range(18):
        try:execute(traffic);connected=True;break
        except ManagerError:time.sleep(.5)
    assert connected,'Старый клиент не переподключился за три минуты'
    passed('ранее работающий 0.1.6 клиент передаёт HTTP после миграции без restart или перевыпуска')
    assert manager.rpc(cfg['components']['wg'],'diagnose')['panel']
    passed('отдельная WG-панель доступна, новый Home admin создан независимо')
    print('SUCCESS: миграция оригинального AWG 0.1.6 в Dual',flush=True)
except BaseException:
    from vpnctl_common import redact
    for name in [prefix+'-awg',prefix+'-wg']:
        value=execute(['docker','logs',name],check=False,merge_stderr=True)
        if value:print(redact(value[-4000:]),flush=True)
        value=execute(['docker','inspect','-f','{{json .State.Health}}',name],check=False)
        if value:print(redact(value),flush=True)
    raise
finally:
    for name in [prefix+'-client',prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

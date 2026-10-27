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
        args=[str(a) for a in args]
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
    def legacy(self):return None
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
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
      '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    manager=IsolatedManager()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):result=manager.install(args)
    cfg=manager.config();containers=[item['container'] for item in cfg['components'].values()]
    assert set(result['components'])=={'awg','wg'}
    passed('fresh install двух контейнеров, healthcheck, системные файлы только во временном root')
    assert manager.install(args)['unchanged'];passed('повторный install сохраняет данные')
    for name,item in cfg['components'].items():
        state=json.loads((Path(item['data_directory'])/'state.json').read_text())
        assert state['clients'][0]['name']=='admin' and state['clients'][0]['networkGroup']=='home'
        password=manager.bootstrap_results[name]['password']
        assert state['auth']['passwordHash'].startswith('$2') and password not in (Path(item['data_directory'])/'state.json').read_text()
        assert manager.rpc(item,'diagnose')['panel']
        assert manager.rpc(item,'client.list')[0]['name']=='admin'
        for candidate in manager.root.rglob('*'):
            if candidate.is_file():assert password.encode() not in candidate.read_bytes()
        manager.rpc(item,'client.add',{'name':'Тестовый Home','group':'home'})
        export=manager.rpc(item,'client.export',{'name':'Тестовый Home','format':'conf'})['value']
        assert '[Interface]' in export
        svg=manager.rpc(item,'client.export',{'name':'Тестовый Home','format':'qr'})['value'];assert '<svg' in svg
        manager.rpc(item,'client.disable',{'name':'Тестовый Home'})
        manager.rpc(item,'client.enable',{'name':'Тестовый Home'})
        manager.rpc(item,'client.remove',{'name':'Тестовый Home'})
        reset=manager.rpc(item,'reset-password');assert len(reset['password'])>=16
        passed(name+': admin Home, bcrypt без сохранения пароля, control CRUD, .conf, QR, reset-password')
    snapshot=manager.backup('wg',str(root/'wg.tar.gz'))['file']
    wg=cfg['components']['wg'];before=(Path(wg['data_directory'])/'state.json').read_bytes()
    manager.rpc(wg,'client.add',{'name':'Удаляется при restore','group':'guest'})
    restore=build_parser().parse_args(['restore','--file',snapshot,'--component','wg','--yes'])
    manager.restore(restore)
    assert (Path(wg['data_directory'])/'state.json').read_bytes()==before
    assert manager.rpc(cfg['components']['awg'],'diagnose')['panel']
    passed('реальный backup/restore WG сохраняет ключи и не перезапускает AWG')
    release=root/'release.json';atomic_json(release,{'version':'0.1.6','images':images})
    update=build_parser().parse_args(['update','all','--manifest',str(release),'--yes'])
    manager.update(update);assert not manager.journal.exists()
    for item in cfg['components'].values():
        assert manager.rpc(item,'diagnose')['panel']
        manager.rpc(item,'client.add',{'name':'После update','group':'guest'})
        manager.rpc(item,'client.remove',{'name':'После update'})
    passed('последовательный update, снятие maintenance, запись после завершения')
    saved=manager.config();original_health=manager.health;failed=[False]
    def fail_once(item):
        if item['backend']=='wireguard' and not failed[0] and json.loads(manager.journal.read_text()).get('phase')=='snapshot_verified':failed[0]=True;raise ManagerError('Искусственный отказ healthcheck')
        return original_health(item)
    manager.health=fail_once
    try:manager.update(build_parser().parse_args(['update','wg','--manifest',str(release),'--yes']))
    except ManagerError:pass
    else:raise AssertionError('Ожидался отказ update')
    manager.health=original_health
    if manager.config()!=saved:
        print('Отличающиеся поля конфигурации: '+str([k for k in set(saved)|set(manager.config()) if saved.get(k)!=manager.config().get(k)]),flush=True)
    if manager.journal.exists():print('Журнал транзакции сохранён, требуется recover',flush=True)
    assert manager.config()==saved and not manager.journal.exists()
    assert manager.rpc(wg,'diagnose')['panel'];passed('отказ healthcheck: реальный rollback образа и matching state')
    print('SUCCESS: испытания manager завершены без изменения сети хоста',flush=True)
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

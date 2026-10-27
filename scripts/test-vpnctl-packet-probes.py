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
    wan=prefix+'-wan'
    execute(['docker','run','-d','--name',wan,'--network','bridge','--cap-add','NET_ADMIN','--entrypoint','sleep',images['wg'],'infinity'])
    wan_ip=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',wan]).strip()
    execute(['docker','exec',wan,'ip','address','add','93.184.216.34/32','dev','lo'])
    execute(['docker','exec',anchor,'ip','route','add','93.184.216.34/32','via',wan_ip])
    execute(['docker','exec','-d',wan,'node','-e',"require('node:http').createServer((q,s)=>s.end('OK')).listen(8080,'0.0.0.0');const d=require('node:dgram').createSocket('udp4');d.on('message',(m,r)=>{const h=Buffer.from(m);h.writeUInt16BE(0x8180,2);h.writeUInt16BE(1,6);d.send(Buffer.concat([h,Buffer.from([192,12,0,1,0,1,0,0,0,30,0,4,93,184,216,34])]),r.port,r.address)});d.bind(53,'93.184.216.34')"])
    for name,item in cfg['components'].items():
        manager.rpc(item,'client.add',{'name':'Пробный Home','group':'home'})
        native=manager.rpc(item,'client.export',{'name':'Пробный Home','format':'conf'})['value']
        import re
        native=re.sub(r'^DNS\s*=.*$','DNS = 93.184.216.34',native,flags=re.M)
        other=cfg['components']['wg' if name=='awg' else 'awg']
        value=manager.packet_probe(item,{'nativeConfig':native,'dns':True,'requests':[
            {'name':'own_panel','url':f"http://{item['address4']}:{item['panel_port']}/api/v1/session",'allow':True},
            {'name':'http_nat','url':'http://93.184.216.34:8080/','allow':True},
            {'name':'foreign_panel','url':f"http://{other['address4']}:{other['panel_port']}/api/v1/session",'allow':False}]})
        assert value.get('packet_proof') and value.get('dns') and all(r['matches'] for r in value['results']),value
        passed(name+': реальная пакетная диагностика own panel, NAT HTTP, DNS, запрет чужой панели')
    print('SUCCESS: испытания manager завершены без изменения сети хоста',flush=True)
finally:
    for name in [prefix+'-awg',prefix+'-wg',prefix+'-wan',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

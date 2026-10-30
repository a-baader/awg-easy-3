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
from vpnctl_common import redact
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
    def health(self,item):
        try:return super().health(item)
        except Exception:
            diagnostics=os.environ.get('TEST_DIAGNOSTIC_DIRECTORY')
            if diagnostics:
                directory=Path(diagnostics);directory.mkdir(parents=True,exist_ok=True)
                info=self.inspect_container(item['container']) or {}
                logs=execute(['docker','logs','--tail','100',item['container']],check=False,merge_stderr=True) or ''
                (directory/(item['container']+'-health.json')).write_text(redact(json.dumps({'state':info.get('State'),'logs':logs},ensure_ascii=False,indent=2)))
            raise
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

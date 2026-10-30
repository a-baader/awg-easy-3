"""Смерть настоящего engine после ready: Docker перезапускает только свой VPN."""
import json
from pathlib import Path
import time

from vpnctl_disposable import (IsolatedManager,PROJECT,root,prefix,anchor,images,
    execute,atomic_json,build_parser,mock,shutil,passed)


class RestartManager(IsolatedManager):
    def write_compose(self,config):
        super().write_compose(config)
        value=json.loads(self.compose_path.read_text())
        for service in value['services'].values():service['restart']='unless-stopped'
        atomic_json(self.compose_path,value)


manager=RestartManager()
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),
        '--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check'):manager.install(args)
    config=manager.config()
    for name,item in config['components'].items():
        other=config['components']['wg' if name=='awg' else 'awg']
        before=manager.inspect_container(item['container'])['RestartCount']
        other_before=manager.inspect_container(other['container'])['RestartCount']
        profile=manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']
        engine='amneziawg-go' if name=='awg' else 'wireguard-go'
        kill_script="const fs=require('node:fs');let found=0;for(const pid of fs.readdirSync('/proc').filter(x=>/^\\d+$/.test(x))){try{if(fs.readFileSync('/proc/'+pid+'/comm','utf8').trim()===process.argv[1]){process.kill(Number(pid),'SIGKILL');found++;}}catch{}}if(found!==1)process.exit(1);"
        execute(['docker','exec',item['container'],'node','-e',kill_script,engine])
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            info=manager.inspect_container(item['container'])
            if info['RestartCount']>before and info['State'].get('Health',{}).get('Status')=='healthy':break
            time.sleep(.5)
        else:raise AssertionError('VPN не восстановился после смерти engine: '+name)
        assert manager.inspect_container(other['container'])['RestartCount']==other_before
        assert manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']==profile
        result=manager.packet_probe(item,{'nativeConfig':profile,'requests':[
            {'name':'panel','url':f"http://{item['address4']}:{item['panel_port']}/api/v1/session",'allow':True}]})
        assert result.get('packet_proof') and all(entry['matches'] for entry in result['results'])
        passed(name+': SIGKILL engine → restart контейнера → прежний профиль и HTTP работают; другой VPN не перезапускался')
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

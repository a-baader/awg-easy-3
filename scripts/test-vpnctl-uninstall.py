"""Проверка T25/T26 с реальным peer-трафиком после удаления соседнего VPN."""
from vpnctl_disposable import (IsolatedManager, root, prefix, anchor, images,
    execute, build_parser, passed, mock, shutil)
import json
manager=IsolatedManager();wan=prefix+'-wan'
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    execute(['docker','run','-d','--name',wan,'--network','bridge','--cap-add','NET_ADMIN','--entrypoint','sleep',images['wg'],'infinity'])
    wan_ip=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',wan]).strip()
    execute(['docker','exec',wan,'ip','address','add','93.184.216.34/32','dev','lo'])
    execute(['docker','exec',anchor,'ip','route','add','93.184.216.34/32','via',wan_ip])
    execute(['docker','exec','-d',wan,'node','-e',"require('node:http').createServer((q,s)=>s.end('OK')).listen(8080,'0.0.0.0')"])
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
    execute(['docker','exec',anchor,'nft','add','table','inet','foreign_sentinel'])
    for removed,survivor in [('wg','awg'),('awg','wg')]:
        if removed=='awg':
            with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
        cfg=manager.config();item=cfg['components'][survivor]
        image=manager.inspect_container(item['container'])['Image']
        manager.uninstall(build_parser().parse_args(['uninstall',removed,'--yes']))
        assert manager.inspect_container(item['container'])['Image']==image
        native=manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']
        result=manager.packet_probe(item,{'nativeConfig':native,'requests':[
            {'name':'panel','url':f"http://{item['address4']}:{item['panel_port']}/api/v1/session",'allow':True},
            {'name':'wan_nat','url':'http://93.184.216.34:8080/','allow':True}]})
        assert result.get('packet_proof') and all(entry['matches'] for entry in result['results']), 'Не прошла передача через оставшийся VPN'
        execute(['docker','exec',anchor,'nft','list','table','inet','foreign_sentinel'])
        passed('uninstall '+removed+': '+survivor+' передаёт peer HTTP к панели и через WAN NAT, чужой firewall сохранён')
    print('SUCCESS: T25/T26 с пакетным подтверждением',flush=True)
finally:
    for name in [prefix+'-awg',prefix+'-wg',wan,anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

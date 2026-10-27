"""Чистая переустановка и полное удаление в одноразовом network namespace."""
from vpnctl_disposable import (IsolatedManager, root, prefix, anchor, images,
    execute, build_parser, passed, mock, shutil, PROJECT)
from vpnctl_common import ManagerError
from pathlib import Path
import json

manager=IsolatedManager();foreign=prefix+'-foreign'
def state(item):return (Path(item['data_directory'])/'state.json').read_bytes()
def profile(item):return manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']
def probe(item,native):
    result=manager.packet_probe(item,{'nativeConfig':native,'requests':[
        {'name':'panel','url':f"http://{item['address4']}:{item['panel_port']}/api/v1/session",'allow':True}]})
    assert result.get('packet_proof') and all(entry['matches'] for entry in result['results']),'Не прошла пакетная проверка панели'
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    execute(['docker','run','-d','--name',foreign,'--network','none','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check'):manager.install(args)
    cfg=manager.config();awg=cfg['components']['awg'];wg=cfg['components']['wg']
    manager.rpc(wg,'client.add',{'name':'Удаляется при переустановке','group':'home'})
    old_wg=state(wg);old_awg=state(awg);old_profile=profile(wg)
    old_bootstrap=(manager.root/'bootstrap/wg.conf').read_bytes()
    old_public=json.loads(old_wg)['server']['publicKey']
    execute(['docker','exec',anchor,'nft','add','table','inet','foreign_sentinel'])
    forwards={key:execute(['docker','exec',anchor,'sysctl','-n',key]).strip() for key in ['net.ipv4.ip_forward','net.ipv6.conf.all.forwarding']}
    reinstall=build_parser().parse_args(['reinstall','wg','--host',host,'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    health=manager.health;failed=[False]
    def fail_once(item):
        if item['backend']=='wireguard' and not failed[0] and json.loads(manager.journal.read_text()).get('phase')=='snapshot_verified':
            failed[0]=True;raise ManagerError('Искусственный отказ новой установки')
        return health(item)
    manager.health=fail_once
    with mock.patch('vpnctl_manager.environment_check'):
        try:manager.reinstall(reinstall)
        except ManagerError as error:assert 'Искусственный' in str(error)
        else:raise AssertionError('Ожидался отказ healthcheck')
    manager.health=health
    assert state(wg)==old_wg and state(awg)==old_awg
    assert (manager.root/'bootstrap/wg.conf').read_bytes()==old_bootstrap
    probe(wg,old_profile)
    passed('отказ reinstall: восстановлены прежние ключи, клиенты и bootstrap; старый WG-профиль передаёт HTTP')
    with mock.patch('vpnctl_manager.environment_check'):result=manager.reinstall(reinstall)
    wg=manager.config()['components']['wg'];new_state=json.loads(state(wg))
    assert new_state['server']['publicKey']!=old_public
    assert [client['name'] for client in new_state['clients']]==['admin']
    assert state(awg)==old_awg and Path(result['snapshot']).exists()
    probe(wg,profile(wg));probe(awg,profile(awg))
    passed('reinstall WG: новые ключи и admin, старые клиенты удалены; WG и неизменённый AWG передают HTTP')
    backup=manager.backup('all')['file']
    result=manager.uninstall(build_parser().parse_args(['uninstall','all','--yes']))
    assert result['vpnctl_removed'] and not manager.path.exists()
    assert not (manager.system_root/'usr/local/sbin/vpnctl').exists()
    assert Path(backup).exists() and Path(result['snapshot']).exists()
    assert not (manager.root/'bootstrap').exists()
    for item in [awg,wg]:
        assert not Path(item['data_directory']).exists()
        assert manager.inspect_container(item['container']) is None
    execute(['docker','exec',anchor,'nft','list','table','inet','foreign_sentinel'])
    assert execute(['docker','inspect','-f','{{.State.Running}}',foreign]).strip()=='true'
    for key,value in forwards.items():assert execute(['docker','exec',anchor,'sysctl','-n',key]).strip()==value
    passed('полное удаление: данные, профили и vpnctl удалены; backup, чужой контейнер/firewall и forwarding сохранены')
    manager.restore(build_parser().parse_args(['restore','--file',backup,'--component','all','--yes']))
    for item in manager.config()['components'].values():probe(item,profile(item))
    passed('после полного удаления оба VPN восстановлены из сохранённого внутреннего backup и передают HTTP')
    print('SUCCESS: контракт переустановки и удаления подтверждён реальными контейнерами',flush=True)
finally:
    for name in [prefix+'-awg',prefix+'-wg',foreign,anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

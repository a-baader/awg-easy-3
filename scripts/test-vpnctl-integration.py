"""Полный lifecycle в одноразовом network namespace без изменения хоста."""
import json
import os
from pathlib import Path
from vpnctl_disposable import (IsolatedManager,PROJECT,root,prefix,anchor,images,
    execute,atomic_json,build_parser,mock,shutil,passed,ManagerError)
from vpnctl_common import redact

containers=[]
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
    release=root/'release.json';atomic_json(release,{'version':(PROJECT/'VERSION').read_text().strip(),'images':images})
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
    execute(['docker','exec',anchor,'nft','add','table','inet','foreign_sentinel'])
    manager.uninstall(build_parser().parse_args(['uninstall','wg','--yes']))
    assert manager.rpc(manager.config()['components']['awg'],'diagnose')['panel']
    execute(['docker','exec',anchor,'nft','list','table','inet','foreign_sentinel'])
    passed('uninstall WG сохраняет AWG и чужую таблицу nftables')
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
    manager.uninstall(build_parser().parse_args(['uninstall','awg','--yes']))
    assert manager.rpc(manager.config()['components']['wg'],'diagnose')['panel']
    execute(['docker','exec',anchor,'nft','list','table','inet','foreign_sentinel'])
    passed('повторная установка WG и uninstall AWG сохраняют WG и чужую таблицу nftables')
    print('SUCCESS: испытания manager завершены без изменения сети хоста',flush=True)
except BaseException:
    # Этот тест пока содержит собственный manager: собираем логи прямо здесь,
    # до удаления контейнеров. Состояние /data и ключи в отчёт не включаются.
    destination=os.environ.get('TEST_DIAGNOSTIC_DIRECTORY')
    if destination:
        directory=Path(destination);directory.mkdir(parents=True,exist_ok=True)
        for name in containers:
            info=manager.inspect_container(name) or {}
            logs=execute(['docker','logs','--tail','150',name],check=False,merge_stderr=True) or ''
            atomic_json(directory/(name+'-health.json'),json.loads(redact(json.dumps({'state':info.get('State'),'logs':logs}))))
    raise
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

"""Обновление исходников и готовых образов с откатом в одноразовой сети."""
from vpnctl_disposable import (IsolatedManager,root,prefix,anchor,images,PROJECT,
    execute,build_parser,mock,shutil,passed)
from pathlib import Path
import json
import os
import subprocess
import vpnctl_source_update as updater
from vpnctl_common import ManagerError

version_parts=[int(part) for part in (PROJECT/'VERSION').read_text().strip().split('.')]
TEST_VERSION='.'.join(map(str,[*version_parts[:2],version_parts[2]+1]))
manager=IsolatedManager();builders=[];tags=[]
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check'):manager.install(args)
    original=manager.config();before={name:(Path(item['data_directory'])/'state.json').read_bytes() for name,item in original['components'].items()}
    sentinel=manager.system_root/'etc/vpn-easy-dual-install-dir';old_sentinel=sentinel.read_bytes()
    # Локальная Git-копия имитирует будущий stable-релиз без публикации на GitHub.
    repository=root/'release-repository';repository.mkdir()
    names=subprocess.check_output(['git','-C',str(PROJECT),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
    for name in names:
        if not name:continue
        source=PROJECT/name
        if not source.is_file():continue
        target=repository/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    (repository/'VERSION').write_text(TEST_VERSION+'\n')
    package=json.loads((repository/'src/package.json').read_text());package['version']=TEST_VERSION
    (repository/'src/package.json').write_text(json.dumps(package)+'\n')
    with (repository/'lib/vpnctl_manager.py').open('a') as stream:stream.write("\nRELEASE_TEST_MARKER='stable fixture'\n")
    for command in [['init','-q'],['config','user.name','Проверка vpnctl'],['config','user.email','vpnctl-test@example.invalid'],['add','.'],['commit','-qm','Тестовый стабильный релиз'],['tag','v'+TEST_VERSION]]:
        execute(['git','-C',str(repository),*command])
    revision=execute(['git','-C',str(repository),'rev-parse','HEAD']).strip()
    release={'version':TEST_VERSION,'source_commit':revision,'platform':'linux/amd64','images':{}}
    # Готовые тестовые образы отличаются только package version; сборки на
    # стороне обновления нет. Исходный ENTRYPOINT сохраняется при docker cp.
    for name in ['awg','wg']:
        builder=execute(['docker','create','--network','none',images[name]]).strip();builders.append(builder)
        execute(['docker','cp',str(repository/'src/package.json'),builder+':/app/package.json'])
        tag='wg-dual-easy-'+name+':source-update-'+prefix;tags.append(tag)
        execute(['docker','commit',builder,tag]);execute(['docker','rm',builder]);builders.remove(builder)
        release['images'][name]=json.loads(execute(['docker','image','inspect',tag]))[0]['Id']
    runner=manager.run
    def local_run(command,**kwargs):
        if command[:2]==['git','clone']:
            command=list(command);command[-2]=repository.as_uri()
        return runner(command,**kwargs)
    manager.run=local_run
    failed=[False];fail_update=[True]
    def factory(namespace,project,previous):
        assert namespace['RELEASE_TEST_MARKER']=='stable fixture'
        assert json.loads(previous.journal.read_text())['owner_pid']==os.getpid()
        target=namespace['Manager'](project,previous.path,runner=previous.run,system_root=previous.system_root)
        # Сохраняем перенаправление Compose в одноразовую сеть.
        target.write_compose=previous.write_compose
        commit=target.commit_transaction
        def fail_once(journal):
            if fail_update[0] and not failed[0]:
                failed[0]=True;raise ManagerError('Искусственный отказ после регистрации нового CLI')
            return commit(journal)
        target.commit_transaction=fail_once
        return target
    with mock.patch('vpnctl_boot.fetch_release',return_value=release),mock.patch.object(updater,'new_manager',side_effect=factory):
        try:manager.update(build_parser().parse_args(['update']))
        except ManagerError as error:assert failed[0],str(error)
        else:raise AssertionError('Ожидался искусственный отказ после регистрации CLI')
        assert sentinel.read_bytes()==old_sentinel and not manager.journal.exists()
        restored=manager.config()
        for name,item in restored['components'].items():
            assert item['image']==original['components'][name]['image']
            assert (Path(item['data_directory'])/'state.json').read_bytes()==before[name]
        passed('сбой после смены CLI: восстановлены прежние исходники CLI, образы и состояния обоих VPN')
        fail_update[0]=False
        result=manager.update(build_parser().parse_args(['update']))
        config=manager.config();source=Path(result['sources_directory'])
        assert source.is_dir() and source!=PROJECT and (PROJECT/'vpnctl').is_file()
        assert config['project']==str(source) and config['source_commit']==revision
        protected=Path(sentinel.read_text().strip())
        assert protected.parent==manager.system_root/'usr/local/lib/wg-dual-easy/sources'
        assert (protected/'VERSION').read_text().strip()==TEST_VERSION
        assert (protected/'_vpnctl_snapshot.json').is_file()
        assert execute(['git','-C',str(source),'rev-parse','HEAD']).strip()==revision
        for name,item in config['components'].items():
            assert item['image']==release['images'][name] and item['version']==TEST_VERSION
            assert (Path(item['data_directory'])/'state.json').read_bytes()==before[name]
            native=manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']
            probe=manager.packet_probe(item,{'nativeConfig':native,'requests':[
                {'name':'panel','url':f"http://{item['address4']}:{item['panel_port']}/api/v1/session",'allow':True}]})
            assert probe.get('packet_proof') and all(entry['matches'] for entry in probe['results'])
        assert not manager.update(build_parser().parse_args(['update']))['updated']
        assert not manager.journal.exists()
        passed('update без опций: исходники точного stable commit, готовые образы, CLI, сохранённые клиенты и HTTP через оба Home VPN; повторный update без изменений')
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor,*builders]:execute(['docker','rm','-f',name],check=False)
    for tag in tags:execute(['docker','image','rm',tag],check=False)
    shutil.rmtree(root,ignore_errors=True)

"""Полное удаление на настоящих контейнерах в одноразовом network namespace."""
from vpnctl_disposable import (IsolatedManager,root,prefix,anchor,images,PROJECT,
    execute,build_parser,mock,shutil,passed)
from pathlib import Path
import hashlib
import json

manager=IsolatedManager();foreign=prefix+'-foreign'
# Одноразовая копия AWG-образа позволяет проверить удаление, не затрагивая
# общие образы остальных проверок или пользовательские теги.
owned_image='wg-dual-easy-awg:purge-'+prefix
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    execute(['docker','run','-d','--name',foreign,'--network','none','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    # Создаём отдельный слой через commit, чтобы удалить именно тестовый image ID.
    builder=execute(['docker','create','--network','none',images['awg']]).strip()
    try:
        image=execute(['docker','commit','--change','LABEL org.opencontainers.image.source=https://github.com/a-baader/wg-dual-easy',builder,owned_image]).strip()
    finally:execute(['docker','rm',builder])
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',owned_image,'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check'):manager.install(args)
    cfg=manager.config();before=manager.path.read_bytes()
    source_hash=hashlib.sha256((PROJECT/'vpnctl').read_bytes()).hexdigest()
    external=root/'external-backup.tar.gz';manager.backup('all',str(external))
    backup=Path(manager.backup('all')['file'])
    manager.restore(build_parser().parse_args(['restore','--file',str(backup),'--component','all','--yes']))
    assert not list(root.rglob('.before-restore-*')), 'Restore оставил неучтённые старые ключи'
    assert backup.exists(), 'Проверенный внутренний backup должен сохраняться'
    before=manager.path.read_bytes()
    passed('restore: оба сервиса готовы, временные копии удалены, внутренний backup сохранён')
    execute(['docker','exec',anchor,'nft','add','table','inet','foreign_sentinel'])
    forwards={key:execute(['docker','exec',anchor,'sysctl','-n',key]).strip() for key in ['net.ipv4.ip_forward','net.ipv6.conf.all.forwarding']}
    purge=build_parser().parse_args(['purge','--yes'])
    result=manager.purge(build_parser().parse_args(['purge','--dry-run']))
    assert result['dry_run'] and not result['purged'] and manager.path.read_bytes()==before
    assert all(manager.inspect_container(item['container'])['State']['Running'] for item in cfg['components'].values())
    passed('purge --dry-run: данные и оба работающих сервиса не изменены')
    # Не удаляем общие неиспользуемые CI-образы: проверка реального удаления
    # ограничена выделенным AWG и используемым чужим контейнером WG.
    with mock.patch('vpnctl_purge.image_candidates',return_value=[image,cfg['components']['wg']['image']]):
        result=manager.purge(purge)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert result['purged'] and result['vpnctl_removed'] and not result['backups_preserved']
    assert not manager.root.exists() and not backup.exists() and external.exists()
    assert not list(root.rglob('.before-restore-*')) and not list(root.rglob('.failed-*'))
    for item in cfg['components'].values():
        assert not Path(item['data_directory']).exists() and manager.inspect_container(item['container']) is None
        assert execute(['docker','exec',anchor,'ip','link','show','dev',item['interface']],check=False) is None
        assert execute(['docker','exec',anchor,'nft','list','table','inet',item['table']],check=False) is None
    assert execute(['docker','image','inspect',image],check=False) is None
    assert image in result['images_removed']
    assert any(item['image']==cfg['components']['wg']['image'] for item in result['images_preserved'])
    assert execute(['docker','inspect','-f','{{.State.Running}}',foreign]).strip()=='true'
    execute(['docker','exec',anchor,'nft','list','table','inet','foreign_sentinel'])
    for key,value in forwards.items():assert execute(['docker','exec',anchor,'sysctl','-n',key]).strip()==value
    assert hashlib.sha256((PROJECT/'vpnctl').read_bytes()).hexdigest()==source_hash
    assert manager.purge(purge)['unchanged'] and not manager.root.exists()
    passed('purge: оба VPN, данные, внутренние backup, vpnctl и выделенный образ удалены; исходники, внешний backup, чужой контейнер/NFT/образ и forwarding сохранены')
finally:
    for name in [prefix+'-awg',prefix+'-wg',foreign,anchor]:execute(['docker','rm','-f',name],check=False)
    execute(['docker','image','rm',owned_image],check=False)
    shutil.rmtree(root,ignore_errors=True)

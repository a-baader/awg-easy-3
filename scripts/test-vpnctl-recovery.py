"""SIGKILL manager и восстановление в disposable окружении; без reboot хоста."""
from vpnctl_disposable import (IsolatedManager, PROJECT, root, prefix, anchor, images,
    execute, atomic_json, build_parser, ManagerError, passed, mock, json, shutil)
import copy
import multiprocessing
import os
from pathlib import Path
import signal
import time
from vpnctl_common import global_lock

class InterruptedManager(IsolatedManager):
    interrupt_at=None
    def save_journal(self,journal):
        super().save_journal(journal)
        if journal['phase']==self.interrupt_at:os.kill(os.getpid(),signal.SIGKILL)
    def compose(self,*args):
        value=super().compose(*args)
        if self.interrupt_at=='before_snapshot' and args==('stop','wg-easy'):os.kill(os.getpid(),signal.SIGKILL)
        if self.interrupt_at=='restore_replaced' and args==('up','-d','--no-deps','wg-easy'):
            if json.loads(self.journal.read_text()).get('snapshot'):os.kill(os.getpid(),signal.SIGKILL)
        return value
    def health(self,item):
        if self.interrupt_at=='rollback_committed' and item['backend']=='wireguard' and not getattr(self,'injected_failure',False):
            if self.journal.exists() and json.loads(self.journal.read_text()).get('phase') in ('snapshot_verified','updated:awg'):
                self.injected_failure=True;raise ManagerError('Искусственный отказ перед rollback')
        return super().health(item)

def update_child(stage,manifest):
    manager=InterruptedManager();manager.interrupt_at=stage
    with global_lock(root/'global.lock',root/'legacy.lock'):
        args=build_parser().parse_args(['update','wg' if stage=='before_snapshot' else 'all','--manifest',str(manifest),'--yes'])
        manager.update(args)

def restore_child(archive):
    manager=InterruptedManager();manager.interrupt_at='restore_replaced'
    with global_lock(root/'global.lock',root/'legacy.lock'):
        manager.restore(build_parser().parse_args(['restore','--file',str(archive),'--component','wg','--yes']))

manager=IsolatedManager();new_tags=[]
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
      '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
    old=manager.config()
    candidate_images={}
    build=root/'build';build.mkdir()
    for name,image in images.items():
        tag=prefix+'-'+name+':candidate';new_tags.append(tag)
        (build/'Dockerfile').write_text('FROM '+image+'\nLABEL test.recovery="'+prefix+'"\n')
        execute(['docker','build','-t',tag,str(build)],env={**os.environ,'DOCKER_BUILDKIT':'0'})
        candidate_images[name]=tag
    manifest=root/'release.json';atomic_json(manifest,{'version':(PROJECT/'VERSION').read_text().strip(),'images':candidate_images})
    for stage in ['before_snapshot','updated:awg','committed','rollback_committed']:
        before=manager.config()
        states={name:(Path(item['data_directory'])/'state.json').read_bytes() for name,item in before['components'].items()}
        process=multiprocessing.get_context('fork').Process(target=update_child,args=(stage,manifest))
        process.start();process.join(timeout=240)
        if process.is_alive():process.kill();process.join();raise AssertionError('Превышено время ожидания точки прерывания')
        assert process.exitcode==-signal.SIGKILL,'Процесс не достиг выбранной точки прерывания'
        journal=json.loads(manager.journal.read_text())
        assert (root/'legacy.lock').is_dir()
        if stage=='committed':
            cfg=manager.config();item=cfg['components']['wg']
            manager.rpc(item,'maintenance.end')
            manager.rpc(item,'client.add',{'name':'Запись после commit','group':'guest'})
            states['wg']=(Path(item['data_directory'])/'state.json').read_bytes()
            for item in cfg['components'].values():execute(['docker','stop',item['container']])
        if stage=='rollback_committed':
            cfg=journal['previous'];manager.write_config(cfg)
            manager.compose('up','-d','--no-deps','wg-easy');manager.health(cfg['components']['wg'])
            manager.rpc(cfg['components']['wg'],'client.add',{'name':'Запись после rollback','group':'guest'})
            states['wg']=(Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes()
            for item in cfg['components'].values():execute(['docker','stop',item['container']])
        with global_lock(root/'global.lock',root/'legacy.lock',recovery_journal=manager.journal):
            manager.rollback(journal)
        assert not manager.journal.exists() and not (root/'legacy.lock').exists()
        assert not list(root.rglob('.failed-*')), 'После recover остались временные копии с ключами'
        assert not list(root.rglob('.before-restore-*')), 'После recover остались старые данные restore'
        cfg=manager.config()
        for name,item in cfg['components'].items():
            assert (Path(item['data_directory'])/'state.json').read_bytes()==states[name],'Не совпало восстановленное состояние'
            assert manager.rpc(item,'diagnose')['panel']
            expected=journal['candidate']['components'][name]['image'] if stage=='committed' else before['components'][name]['image']
            assert manager.inspect_container(item['container'])['Image']==expected
        if stage=='committed':
            assert any(c['name']=='Запись после commit' for c in manager.rpc(cfg['components']['wg'],'client.list'))
        manager.rpc(cfg['components']['wg'],'client.add',{'name':'После recover','group':'guest'})
        manager.rpc(cfg['components']['wg'],'client.remove',{'name':'После recover'})
        passed('SIGKILL '+stage+': lock, образ, состояние, панель и запись после recover')
    archive=manager.backup('wg',str(root/'restore-input.tar.gz'))['file']
    cfg=manager.config();wg=cfg['components']['wg']
    manager.rpc(wg,'client.add',{'name':'До восстановления','group':'guest'})
    before=(Path(wg['data_directory'])/'state.json').read_bytes()
    process=multiprocessing.get_context('fork').Process(target=restore_child,args=(archive,))
    process.start();process.join(timeout=180)
    if process.is_alive():process.kill();process.join();raise AssertionError('Не достигнута точка прерывания restore')
    assert process.exitcode==-signal.SIGKILL
    journal=json.loads(manager.journal.read_text())
    with global_lock(root/'global.lock',root/'legacy.lock',recovery_journal=manager.journal):manager.rollback(journal)
    assert (Path(wg['data_directory'])/'state.json').read_bytes()==before
    assert manager.rpc(wg,'diagnose')['panel'] and manager.rpc(cfg['components']['awg'],'diagnose')['panel']
    assert not manager.journal.exists()
    assert not list(root.rglob('.failed-*')), 'После rollback restore остались временные ключи'
    assert not list(root.rglob('.before-restore-*')), 'После rollback restore остались старые ключи'
    passed('SIGKILL после замены каталога restore: подтверждённые до restore данные восстановлены, обе панели работают')
    print('SUCCESS: recovery до snapshot, после первого компонента, после commit и при restore',flush=True)
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    for tag in new_tags:execute(['docker','image','rm',tag],check=False)
    shutil.rmtree(root,ignore_errors=True)

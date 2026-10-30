"""Host manager: проверки на временных данных, без команд на реальном хосте."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from vpnctl_manager import Manager,build_parser,main
from vpnctl_common import ManagerError,atomic_json,atomic_write,global_lock
from vpnctl_backup import create_archive,verify_archive,extract_component

PROJECT=Path(__file__).resolve().parents[1]
OLD='sha256:'+'1'*64
BAD='sha256:'+'f'*64

def config(root):
    components={}
    for name,n,backend,table,panel,port in [('awg',8,'amneziawg','awg_easy_3',51821,39516),('wg',9,'wireguard','wg_easy_3',51825,51824)]:
        directory=root/(name+'-data');directory.mkdir(mode=0o700)
        atomic_json(directory/'state.json',{'version':1 if name=='awg' else 3,'server':{'backend':backend},'private_test_marker':name+'-TEST-DATA'})
        components[name]={'backend':backend,'table':table,'container':'awg-easy-3' if name=='awg' else 'wg-easy-dual',
            'interface':'awg0' if name=='awg' else 'wg0','address4':f'10.{n}.0.1','network4':f'10.{n}.0.0/24',
            'udp_port':port,'panel_port':panel,'data_directory':str(directory),'image':OLD,'image_ref':OLD}
    return {'config_version':1,'version':'0.1.6','language':'ru','project':str(PROJECT),'components':components}

class FakeManager(Manager):
    def install_sources(self):return self.project
    def __init__(self,root):
        super().__init__(PROJECT,root/'manager/config.json',runner=self.fake,system_root=root/'system')
        self.calls=[];self.busy=set();self.fail_image=None
    def fake(self,args,**kwargs):
        self.calls.append(args)
        if args[0]=='ss':
            protocol='udp' if 'u' in args[1] else 'tcp';port=int(args[-1].rsplit(':',1)[1])
            return 'занято' if (protocol,port) in self.busy else ''
        if args[:2]==['docker','ps']:return ''
        if args[0]=='ip' and 'address' in args:return '[{"addr_info":[{"local":"192.0.2.10","scope":"global"}]}]'
        if args[0]=='ip':return '[{"dst":"default","dev":"eth0"}]' if '-4' in args else '[]'
        return ''
    def legacy(self):return None
    def image(self,reference,backend):return reference
    def resolve_image_reference(self,reference):return reference
    def inspect_container(self,name):return None
    def compose(self,*args):self.calls.append(['compose',*args]);return ''
    def validate_state_image(self,item,directory=None):return None
    def validate_release_image(self,item,version):return None
    def check_plan(self,candidate,previous=None):return None
    def health(self,item):
        if item['image']==self.fail_image:
            atomic_json(Path(item['data_directory'])/'state.json',{'version':999,'invalid':'TEST'})
            raise ManagerError('Искусственный отказ healthcheck')

class Checks(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='vpnctl-test-');self.root=Path(self.temp.name);self.manager=FakeManager(self.root)
    def tearDown(self):self.temp.cleanup()
    def test_plan_random_port_and_strict_explicit_port(self):
        args=build_parser().parse_args(['install','--host','192.0.2.10','--data-root',str(self.root/'data')])
        result=self.manager.plan_install(args)
        self.assertNotIn(result['components']['awg']['udp_port'],[51820,51824])
        self.assertEqual(result['components']['wg']['panel_port'],51825)
        self.assertTrue(result['auto_check'])
        self.manager.busy.add(('udp',39517));args.awg_port=39517
        with self.assertRaisesRegex(ManagerError,'Явно'):self.manager.plan_install(args)
        args.awg_port=0
        with self.assertRaisesRegex(ManagerError,'Порт'):self.manager.plan_install(args)
    def test_default_port_conflict_offers_choice(self):
        self.manager.busy.add(('tcp',51821))
        args=build_parser().parse_args(['install','--host','192.0.2.10','--data-root',str(self.root/'data')])
        with mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',return_value='51830'):
            plan=self.manager.plan_install(args)
        self.assertEqual(plan['components']['awg']['panel_port'],51830)
    def test_port_scan_handles_only_confirmed_disappeared_containers(self):
        def run(args,**kwargs):
            if args[:3]==['docker','ps','-q']:return 'temporary-id\n'
            if args[:2]==['docker','inspect']:return None
            return ''
        self.manager.run=run
        self.assertFalse(self.manager.port_used('udp',39517))
        def exists(args,**kwargs):
            if args[:3]==['docker','ps','-aq']:return 'temporary-id\n'
            return run(args,**kwargs)
        self.manager.run=exists
        with self.assertRaisesRegex(ManagerError,'порты'):self.manager.port_used('udp',39517)
    def test_backup_restore_one_component_preserves_other(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        before_awg=(Path(cfg['components']['awg']['data_directory'])/'state.json').read_bytes()
        before_wg=(Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes()
        output=self.root/'copy.tar.gz';self.manager.backup('wg',str(output))
        self.assertEqual(output.stat().st_mode&0o777,0o600)
        self.assertEqual(verify_archive(output)['selected'],['wg'])
        target=Path(cfg['components']['wg']['data_directory'])/'state.json';atomic_json(target,{'version':3,'server':{'backend':'wireguard'},'marker':'CHANGED'})
        args=build_parser().parse_args(['restore','--file',str(output),'--component','wg','--yes'])
        self.manager.restore(args)
        self.assertEqual(target.read_bytes(),before_wg)
        self.assertEqual((Path(cfg['components']['awg']['data_directory'])/'state.json').read_bytes(),before_awg)
        self.assertFalse(any('awg-easy' in call for call in self.manager.calls))
    def test_failed_update_restores_matching_image_and_state(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        target=Path(cfg['components']['wg']['data_directory'])/'state.json';before=target.read_bytes()
        release=self.root/'release.json';atomic_json(release,{'version':'0.2.0','images':{'wg':BAD}})
        self.manager.fail_image=BAD
        args=build_parser().parse_args(['update','wg','--manifest',str(release),'--yes'])
        with self.assertRaisesRegex(ManagerError,'Искусственный'):self.manager.update(args)
        self.assertEqual(target.read_bytes(),before)
        self.assertEqual(self.manager.config()['components']['wg']['image'],OLD)
        self.assertFalse(self.manager.journal.exists())
    def test_manifest_version_must_match_actual_image_package(self):
        self.manager.run=lambda args,**kwargs:'0.1.6\n'
        Manager.validate_release_image(self.manager,{'image':OLD},'0.1.6')
        with self.assertRaisesRegex(ManagerError,'не совпадает'):
            Manager.validate_release_image(self.manager,{'image':OLD},'0.2.0')
        self.assertFalse(self.manager.path.exists());self.assertFalse(self.manager.journal.exists())
    def test_legacy_migration_rejects_experimental_version_before_data_changes(self):
        cfg=config(self.root);directory=cfg['components']['awg']['data_directory']
        self.manager.inspect_container=lambda name:{'Image':OLD,'HostConfig':{'NetworkMode':'host'},
            'Mounts':[{'Source':directory,'Destination':'/data'}],
            'Config':{'Labels':{'com.docker.compose.service':'awg-easy','com.docker.compose.project.working_dir':str(self.root)},'Env':[]}}
        self.manager.run=lambda args,**kwargs:'0.3.0\n'
        self.manager.validate_release_image=lambda item,version:Manager.validate_release_image(self.manager,item,version)
        before=(Path(directory)/'state.json').read_bytes()
        with self.assertRaisesRegex(ManagerError,'исходный AWG 0.1.6'):Manager.legacy(self.manager)
        self.assertEqual((Path(directory)/'state.json').read_bytes(),before)
        self.assertFalse(self.manager.journal.exists())
    def test_recovery_after_commit_keeps_new_state(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        candidate=copy.deepcopy(cfg);candidate['components']['wg']['image']=BAD
        target=Path(cfg['components']['wg']['data_directory'])/'state.json'
        snapshot=self.manager.backup('wg',str(self.root/'before-commit.tar.gz'))['file']
        atomic_json(target,{'version':3,'server':{'backend':'wireguard'},'acknowledged':'Запись после фиксации'})
        before=target.read_bytes()
        journal={'operation':'update','phase':'committed','previous':cfg,'candidate':candidate,'selected':['wg'],'snapshot':snapshot}
        self.manager.save_journal(journal)
        self.manager.rollback(journal)
        self.assertEqual(self.manager.config()['components']['wg']['image'],BAD)
        self.assertEqual(target.read_bytes(),before)
        self.assertNotIn('maintenance_components',self.manager.config())
        self.assertFalse(self.manager.journal.exists())
        self.assertIn(['compose','up','-d','--no-deps','wg-easy'],self.manager.calls)
    def test_repeated_recover_after_rollback_preserves_new_acknowledged_write(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        snapshot=self.manager.backup('wg',str(self.root/'before-rollback.tar.gz'))['file']
        target=Path(cfg['components']['wg']['data_directory'])/'state.json'
        atomic_json(target,{'version':3,'server':{'backend':'wireguard'},'acknowledged':'Запись после rollback'})
        before=target.read_bytes()
        candidate=copy.deepcopy(cfg);candidate['components']['wg']['image']=BAD
        journal={'operation':'update','phase':'rollback_committed','previous':cfg,'candidate':candidate,'selected':['wg'],'snapshot':snapshot}
        self.manager.save_journal(journal);self.manager.rollback(journal)
        self.assertEqual(target.read_bytes(),before);self.assertEqual(self.manager.config()['components']['wg']['image'],OLD)
        self.assertFalse(self.manager.journal.exists())
    def test_uncertain_commit_storage_failure_keeps_maintenance_and_requires_recover(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        journal={'operation':'update','phase':'snapshot_verified','previous':cfg,'candidate':cfg,'selected':['wg']}
        self.manager.save_journal(journal)
        with mock.patch.object(self.manager,'save_journal',side_effect=OSError('Искусственный отказ fsync')):
            with self.assertRaises(OSError):self.manager.commit_transaction(journal)
        with self.assertRaises(ManagerError) as caught:self.manager.rollback(journal)
        self.assertEqual(caught.exception.code,75)
        self.assertEqual(self.manager.config()['maintenance_components'],['wg'])
        self.assertTrue(self.manager.journal.exists());self.assertFalse(self.manager.calls)
    def test_legacy_recovery_before_snapshot_restarts_original_without_state_changes(self):
        cfg=config(self.root);item=cfg['components']['awg']
        previous=copy.deepcopy(cfg);previous['components']={'awg':item};previous['legacy_origin']={'running':True}
        before=(Path(item['data_directory'])/'state.json').read_bytes()
        journal={'operation':'install','phase':'prepared','previous':previous,'candidate':cfg,'selected':['awg','wg']}
        def inspect(name):
            if name!=item['container']:return None
            return {'Image':OLD,'Mounts':[{'Source':item['data_directory'],'Destination':'/data'}],
                'HostConfig':{'NetworkMode':'host'},'Config':{'Labels':{'com.docker.compose.service':'awg-easy'}},'State':{'Running':False}}
        self.manager.inspect_container=inspect;self.manager.save_journal(journal)
        self.manager.rollback(journal)
        self.assertEqual((Path(item['data_directory'])/'state.json').read_bytes(),before)
        self.assertIn(['docker','start',item['container']],self.manager.calls)
        self.assertFalse(self.manager.journal.exists())
    def test_recovery_rejects_foreign_container_before_mutation(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        before=self.manager.path.read_bytes()
        journal={'operation':'update','phase':'committed','previous':cfg,'candidate':cfg,'selected':['wg']}
        self.manager.inspect_container=lambda name:{'Image':BAD,'Mounts':[],'HostConfig':{'NetworkMode':'host'},'Config':{'Labels':{}}}
        self.manager.save_journal(journal)
        with self.assertRaisesRegex(ManagerError,'владение'):self.manager.rollback(journal)
        self.assertEqual(self.manager.path.read_bytes(),before)
        self.assertFalse(self.manager.calls);self.assertTrue(self.manager.journal.exists())
    def test_control_mutation_rejects_foreign_mount_before_docker_exec(self):
        cfg=config(self.root);item=cfg['components']['wg']
        info={'Image':OLD,'Mounts':[{'Source':'/foreign-data','Destination':'/data'}],
            'HostConfig':{'NetworkMode':'host'},'Config':{'Labels':{'com.docker.compose.service':'wg-easy'}}}
        self.manager.inspect_container=lambda name:info
        with self.assertRaisesRegex(ManagerError,'владение'):self.manager.rpc(item,'client.remove',{'name':'admin'})
        self.assertFalse(self.manager.calls)
        info['Mounts'][0]['Source']=item['data_directory']
        calls=[]
        def run(args,**kwargs):calls.append((args,kwargs));return '{"ok":true,"value":{"accepted":true}}'
        self.manager.run=run
        self.assertTrue(self.manager.rpc(item,'client.add',{'name':'Тестовый','group':'guest'})['accepted'])
        self.assertEqual(calls[0][0][:3],['docker','exec','-i'])
        self.assertEqual(json.loads(calls[0][1]['input'])['args']['name'],'Тестовый')
    def test_partial_registration_rolls_back_only_owned_files(self):
        cfg=config(self.root)
        journal={'operation':'install','phase':'prepared','previous':None,'candidate':cfg,'selected':['awg','wg']}
        self.manager.save_journal(journal)
        files=list(self.manager.system_files())
        def fail_second(path,*args,**kwargs):
            if Path(path)==files[1]:raise OSError('Искусственный отказ записи системного файла')
            return atomic_write(path,*args,**kwargs)
        with mock.patch('vpnctl_system.atomic_write',side_effect=fail_second):
            with self.assertRaises(OSError):self.manager.register(cfg)
        self.assertTrue(files[0].exists());self.assertFalse(files[1].exists())
        self.manager.cleanup_created=lambda item:None
        self.manager.rollback(journal)
        self.assertTrue(all(not path.exists() for path in files))
        self.assertFalse(self.manager.journal.exists())
    def test_recovery_does_not_overwrite_modified_system_file(self):
        cfg=config(self.root)
        journal={'operation':'install','phase':'prepared','previous':None,'candidate':cfg,'selected':['wg']}
        self.manager.save_journal(journal);self.manager.register(cfg)
        file=next(iter(self.manager.system_files()));file.write_text('Изменено администратором\n')
        saved=json.loads(self.manager.journal.read_text())
        with self.assertRaisesRegex(ManagerError,'другим приложением'):self.manager.restore_manager_files(saved)
        self.assertEqual(file.read_text(),'Изменено администратором\n')
    def test_corrupt_snapshot_does_not_replace_state_or_stop_containers(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        snapshot=self.manager.backup('wg',str(self.root/'damaged.tar.gz'))['file']
        Path(snapshot).write_bytes(b'NOT AN ARCHIVE')
        before=(Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes()
        self.manager.calls.clear()
        journal={'operation':'restore','phase':'snapshot_verified','previous':cfg,'candidate':cfg,'selected':['wg'],'snapshot':snapshot}
        self.manager.save_journal(journal)
        with self.assertRaises(ManagerError):self.manager.rollback(journal)
        self.assertFalse(self.manager.calls);self.assertTrue(self.manager.journal.exists())
        self.assertEqual((Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes(),before)
    def test_snapshot_failure_clears_maintenance_without_data_changes(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        before=(Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes()
        journal={'operation':'update','phase':'prepared','previous':cfg,'candidate':cfg,'selected':['wg']}
        frozen=self.manager.freeze(journal)
        self.assertEqual(frozen['maintenance_components'],['wg'])
        self.assertEqual(json.loads(self.manager.compose_path.read_text())['services']['wg-easy']['environment']['VPN_MAINTENANCE'],'1')
        self.manager.rollback(journal)
        self.assertNotIn('maintenance_components',self.manager.config())
        self.assertEqual((Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes(),before)
    def test_archive_rejects_paths_and_links_before_extraction(self):
        for name,kind in [('../escape',tarfile.REGTYPE),('awg/data/link',tarfile.SYMTYPE),('awg/data/hard',tarfile.LNKTYPE)]:
            with self.subTest(name=name):
                target=self.root/(str(len(name))+str(kind)+'.tar.gz')
                with tarfile.open(target,'w:gz') as archive:
                    item=tarfile.TarInfo(name);item.type=kind;item.linkname='/etc/shadow';item.size=0;archive.addfile(item,io.BytesIO())
                with self.assertRaises(ManagerError):verify_archive(target)
        self.assertFalse((self.root/'escape').exists())
    def test_lock_does_not_steal_another_operation(self):
        lock=self.root/'global.lock';legacy=self.root/'legacy.lock'
        with global_lock(lock,legacy):
            with self.assertRaises(ManagerError):
                with global_lock(lock,legacy):pass
            self.assertTrue((legacy/'pid').is_file())
        self.assertFalse(legacy.exists())
        legacy.mkdir()
        with self.assertRaises(ManagerError):
            with global_lock(lock,legacy):pass
        self.assertTrue(legacy.exists())
    def test_recover_reclaims_only_known_dead_lock_owner(self):
        lock=self.root/'global.lock';legacy=self.root/'legacy.lock';journal=self.root/'transaction.json'
        legacy.mkdir();(legacy/'pid').write_text('99999999\n');atomic_json(journal,{'lock_owner_pid':99999999})
        with global_lock(lock,legacy,recovery_journal=journal):self.assertEqual((legacy/'pid').read_text().strip(),str(os.getpid()))
        self.assertFalse(legacy.exists())
        legacy.mkdir();(legacy/'pid').write_text(str(os.getpid()));atomic_json(journal,{'lock_owner_pid':os.getpid()})
        with self.assertRaises(ManagerError):
            with global_lock(lock,legacy,recovery_journal=journal):pass
        self.assertTrue(legacy.exists())
    def test_idempotent_install_does_not_reinitialize_data(self):
        cfg=config(self.root);self.manager.write_config(cfg)
        args=build_parser().parse_args(['install'])
        result=self.manager.install(args)
        self.assertTrue(result['unchanged']);self.assertFalse(self.manager.calls)
    def test_cli_existing_install_requires_explicit_action_without_legacy_detection(self):
        self.manager.write_config(config(self.root));output=io.StringIO()
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('vpnctl_manager.os.geteuid',return_value=0),mock.patch.object(self.manager,'legacy',side_effect=AssertionError('Legacy не должен проверяться')):
            with mock.patch('vpnctl_manager.global_lock',return_value=global_lock(self.root/'lock',self.root/'legacy-lock')),mock.patch('sys.stdout',output):
                self.assertEqual(main(PROJECT,['install','--yes','--json']),64)
        self.assertEqual(output.getvalue(),'')
        self.assertFalse(self.manager.calls)
    def test_bootstrap_does_not_persist_panel_password(self):
        cfg=config(self.root)
        item=cfg['components']['wg'];directory=self.root/'new-wg';item['data_directory']=str(directory)
        item.update({'endpoint':'192.0.2.1','wan_interface':'eth0'})
        seen=[]
        def run(args,**kwargs):
            seen.append(args)
            options=json.loads(kwargs['input']);self.assertEqual(options['firstClientName'],'admin')
            return json.dumps({'nativeConfig':'[Interface]\nPrivateKey = TEST\n','bootstrapPassword':'ONLY-IN-MEMORY-123','panelUrl':'http://10.9.0.1:51825','vpnLink':None})
        self.manager.run=run;self.manager.initialize('wg',item,cfg)
        self.assertEqual(self.manager.bootstrap_results['wg']['password'],'ONLY-IN-MEMORY-123')
        for file in self.manager.root.rglob('*'):
            if file.is_file():self.assertNotIn(b'ONLY-IN-MEMORY-123',file.read_bytes())
        self.assertTrue(all('ONLY-IN-MEMORY-123' not in str(args) for args in seen))

if __name__=='__main__':unittest.main()

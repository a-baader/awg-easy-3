"""Контракт purge на временных данных, без изменения настоящего хоста."""
import copy
from contextlib import nullcontext
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_manager
import vpnctl_purge
import vpnctl_retired
from vpnctl_common import ManagerError,atomic_json
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))
OLD=fixture['OLD']

class Purge(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vpnctl-purge-');self.root=Path(self.temp.name)
        self.manager=fixture['FakeManager'](self.root)
        original=self.manager.run
        def run(args,**kwargs):
            if args[:3] in (['ip','link','show'],['nft','list','table']):return None
            return original(args,**kwargs)
        self.manager.run=run
    def tearDown(self):self.temp.cleanup()
    def args(self,*extra):return vpnctl_manager.parse_arguments(PROJECT,['purge',*extra])[1]
    def installed(self):
        config=fixture['config'](self.root);self.manager.register(config)
        return self.manager.config()
    def purge(self):
        with mock.patch('sys.stderr',io.StringIO()):return self.manager.purge(self.args('--yes'))
    def test_confirmation_before_any_mutation(self):
        cfg=self.installed();before=self.manager.path.read_bytes()
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=False),mock.patch('sys.stderr',io.StringIO()):
            self.assertEqual(vpnctl_manager.main(PROJECT,['purge']),64)
        self.assertEqual(self.manager.path.read_bytes(),before)
        self.assertTrue(all(Path(item['data_directory']).exists() for item in cfg['components'].values()))
    def test_dry_run_does_not_write_or_stop(self):
        cfg=self.installed();before=self.manager.path.read_bytes()
        result=self.manager.purge(self.args('--dry-run'))
        self.assertTrue(result['dry_run']);self.assertFalse(result['purged'])
        self.assertEqual(set(result['remove']),{'awg','wg'})
        self.assertEqual(self.manager.path.read_bytes(),before);self.assertFalse(self.manager.journal.exists())
        self.assertFalse(any(call[0]=='compose' or call[:3]==['docker','image','rm'] for call in self.manager.calls))
    def test_removes_all_working_data_backups_cli_preserves_source_and_external(self):
        cfg=self.installed();backup=self.manager.backup('all')['file']
        external=self.root/'external.tar.gz';self.manager.backup('all',str(external))
        (self.manager.root/'backups/corrupt.tar.gz').write_bytes('повреждённый backup'.encode())
        (self.manager.root/'releases').mkdir();(self.manager.root/'releases/v0.json').write_text('{}')
        self.manager.log_result('test','ok')
        source=(PROJECT/'vpnctl').read_bytes()
        result=self.purge()
        self.assertTrue(result['purged']);self.assertTrue(result['vpnctl_removed'])
        self.assertFalse(result['backups_preserved']);self.assertFalse(Path(backup).exists())
        self.assertFalse(self.manager.root.exists());self.assertTrue(external.exists())
        self.assertEqual((PROJECT/'vpnctl').read_bytes(),source)
        for item in cfg['components'].values():self.assertFalse(Path(item['data_directory']).exists())
        self.assertFalse(any(call[0]=='sysctl' or 'prune' in call or '--force' in call or 'down' in call for call in self.manager.calls))
    def test_repeat_is_noop_and_does_not_recreate_manager(self):
        self.installed();self.purge();self.manager.calls.clear()
        self.assertTrue(self.purge()['unchanged']);self.assertFalse(self.manager.root.exists());self.assertEqual(self.manager.calls,[])
    def test_restore_then_purge_leaves_no_unarchived_secrets(self):
        cfg=self.installed();archive=self.manager.backup('all')['file']
        target=Path(cfg['components']['wg']['data_directory'])/'state.json'
        target.write_text('{"version":3,"server":{"backend":"wireguard"},"secret":"Старый секрет"}')
        self.manager.restore(vpnctl_manager.build_parser().parse_args(['restore','--file',archive,'--component','all','--yes']))
        self.assertEqual(list(self.root.glob('.before-restore-*')),[])
        self.assertTrue(Path(archive).exists())
        self.purge()
        self.assertFalse(self.manager.root.exists())
        self.assertFalse(list(self.root.glob('.failed-*')))
    def test_failed_update_cleans_retired_data_and_preserves_snapshot(self):
        cfg=self.installed();release=self.root/'release.json'
        atomic_json(release,{'version':'0.2.0','images':{'wg':fixture['BAD']}})
        self.manager.fail_image=fixture['BAD']
        with self.assertRaisesRegex(ManagerError,'Искусственный'):
            self.manager.update(vpnctl_manager.build_parser().parse_args(['update','wg','--manifest',str(release),'--yes']))
        self.assertFalse(list(self.root.glob('.failed-*')))
        self.assertTrue(list((self.manager.root/'backups').glob('*.tar.gz')))
        self.assertFalse(self.manager.journal.exists())
    def test_cleanup_failure_keeps_committed_journal_for_recovery(self):
        self.installed();archive=self.manager.backup('all')['file']
        with mock.patch('vpnctl_retired.shutil.rmtree',side_effect=OSError('Искусственный отказ очистки')):
            with self.assertRaisesRegex(OSError,'Искусственный'):
                self.manager.restore(vpnctl_manager.build_parser().parse_args(['restore','--file',archive,'--component','all','--yes']))
        journal=json.loads(self.manager.journal.read_text())
        self.assertEqual(journal['phase'],'committed')
        self.assertEqual(len(list(self.root.glob('.before-restore-*'))),2)
        state=self.manager.config()['components']['wg']['data_directory']
        (Path(state)/'after-commit').write_text('Новые данные сохраняются')
        self.manager.rollback(journal)
        self.assertFalse(self.manager.journal.exists())
        self.assertFalse(list(self.root.glob('.before-restore-*')))
        self.assertTrue((Path(state)/'after-commit').exists())
    def test_retire_intent_survives_interruption_before_rename(self):
        cfg=self.installed();target=Path(cfg['components']['wg']['data_directory'])
        journal={'operation':'restore','phase':'prepared','previous':cfg,'candidate':cfg,'selected':['wg']}
        with mock.patch('vpnctl_retired.durable_replace',side_effect=OSError('Искусственный отказ rename')):
            with self.assertRaises(OSError):vpnctl_retired.retire(self.manager,journal,target,'.before-restore-')
        saved=json.loads(self.manager.journal.read_text())
        self.assertEqual(len(saved['retired_data']),1)
        vpnctl_retired.cleanup(self.manager,saved)
        self.assertTrue((target/'state.json').exists())
    def test_retired_symlink_or_replaced_directory_is_not_deleted(self):
        cfg=self.installed();target=Path(cfg['components']['wg']['data_directory'])
        journal={'previous':cfg,'candidate':cfg,'selected':['wg']}
        vpnctl_retired.retire(self.manager,journal,target,'.failed-')
        retired=Path(journal['retired_data'][0]['path'])
        saved=self.root/'saved';retired.rename(saved)
        retired.mkdir();(retired/'foreign').write_text('Чужие данные')
        with self.assertRaisesRegex(ManagerError,'подменена'):vpnctl_retired.cleanup(self.manager,journal)
        self.assertTrue((retired/'foreign').exists())
        (retired/'foreign').unlink();retired.rmdir();retired.symlink_to(saved,target_is_directory=True)
        with self.assertRaisesRegex(ManagerError,'владение'):vpnctl_retired.cleanup(self.manager,journal)
        self.assertTrue((saved/'state.json').exists())
    def test_untracked_legacy_and_foreign_directories_are_preserved(self):
        self.installed()
        legacy=self.root/('.before-restore-'+'a'*32);legacy.mkdir()
        (legacy/'foreign').write_text('Нет доказательства принадлежности')
        self.purge()
        self.assertTrue((legacy/'foreign').exists())
    def test_failed_fresh_restore_and_extend_clean_created_data(self):
        for operation in ['restore','extend']:
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);manager=fixture['FakeManager'](root)
                original=manager.run
                def run(args,**kwargs):
                    if args[:3] in (['ip','link','show'],['nft','list','table']):return None
                    return original(args,**kwargs)
                manager.run=run
                candidate=fixture['config'](root)
                previous=copy.deepcopy(candidate) if operation=='extend' else None
                if previous:previous['components'].pop('wg')
                manager.write_config(candidate)
                journal={'operation':operation,'phase':'prepared','previous':previous,'candidate':candidate,
                         'selected':['wg'] if previous else ['awg','wg']}
                manager.save_journal(journal);manager.rollback(journal)
                self.assertFalse(manager.journal.exists())
                self.assertFalse(list(root.glob('.failed-*')))
                self.assertFalse(Path(candidate['components']['wg']['data_directory']).exists())
                self.assertEqual(Path(candidate['components']['awg']['data_directory']).exists(),bool(previous))
    def test_after_uninstall_removes_retained_backups(self):
        self.installed()
        with mock.patch('sys.stderr',io.StringIO()):self.manager.uninstall(vpnctl_manager.parse_arguments(PROJECT,['uninstall','all','--yes'])[1])
        self.assertTrue((self.manager.root/'backups').exists())
        self.purge();self.assertFalse(self.manager.root.exists())
    def test_symlink_to_foreign_backups_is_rejected_before_stopping(self):
        self.installed();foreign=self.root/'foreign';foreign.mkdir();(foreign/'keep').write_text('чужие данные')
        (self.manager.root/'backups').symlink_to(foreign,target_is_directory=True)
        with self.assertRaisesRegex(ManagerError,'символьная'):self.purge()
        self.assertEqual((foreign/'keep').read_text(),'чужие данные');self.assertFalse(any(call[0]=='compose' for call in self.manager.calls))
    def test_project_as_data_directory_is_rejected(self):
        cfg=self.installed();cfg['components']['wg']['data_directory']=str(PROJECT);self.manager.write_config(cfg)
        with self.assertRaisesRegex(ManagerError,'Небезопасный'):self.purge()
    def test_foreign_container_aborts_before_backup_or_stop(self):
        self.installed();self.manager.inspect_container=lambda name:{'Mounts':[],'Config':{},'HostConfig':{},'Image':'foreign'}
        with self.assertRaisesRegex(ManagerError,'владение'):self.purge()
        self.assertFalse((self.manager.root/'backups').exists());self.assertFalse(any(call[0]=='compose' for call in self.manager.calls))
    def test_modified_system_file_and_unknown_manager_file_preserved(self):
        self.installed();foreign=self.manager.root/'foreign.txt';foreign.write_text('чужое')
        command=self.manager.system_root/'usr/local/sbin/vpnctl';command.write_text('изменён администратором')
        result=self.purge();self.assertFalse(result['vpnctl_removed'])
        self.assertEqual(command.read_text(),'изменён администратором');self.assertEqual(foreign.read_text(),'чужое')
    def test_postcommit_failure_requires_recovery_without_restoring_clients(self):
        cfg=self.installed()
        with mock.patch('vpnctl_purge.remove_images',side_effect=ManagerError('Искусственный сбой очистки')):
            with self.assertRaisesRegex(ManagerError,'Искусственный'):self.purge()
        journal=json.loads(self.manager.journal.read_text());self.assertEqual(journal['phase'],'committed')
        self.assertTrue(journal['purge']);self.assertFalse(self.manager.path.exists())
        self.assertFalse(any(Path(item['data_directory']).exists() for item in cfg['components'].values()))
        with mock.patch('sys.stderr',io.StringIO()):self.manager.rollback(journal)
        self.assertFalse(self.manager.root.exists())
    def test_recovery_after_backups_already_deleted(self):
        self.installed();unlink=Path.unlink;failed=[False]
        def fail_once(path,*args,**kwargs):
            if path==self.manager.journal and not failed[0]:
                failed[0]=True;raise OSError('Искусственный отказ удаления журнала')
            return unlink(path,*args,**kwargs)
        with mock.patch.object(Path,'unlink',fail_once):
            with self.assertRaises(OSError):self.purge()
        self.assertFalse((self.manager.root/'backups').exists());self.assertTrue(self.manager.journal.exists())
        with mock.patch('sys.stderr',io.StringIO()):self.manager.rollback(json.loads(self.manager.journal.read_text()))
        self.assertFalse(self.manager.root.exists())
    def test_images_removed_only_when_owned_and_unused(self):
        foreign='sha256:'+'2'*64;used='sha256:'+'3'*64;calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['docker','image','inspect']:
                return json.dumps([{'Id':args[-1],'Config':{'Labels':{'org.opencontainers.image.source':'foreign' if args[-1]==foreign else 'https://github.com/a-baader/wg-dual-easy'}}}])
            if args[:3]==['docker','ps','-aq']:return 'foreign-container' if args[-1].endswith(used) else ''
            return ''
        self.manager.run=run
        removed,preserved=vpnctl_purge.remove_images(self.manager,[OLD,foreign,used])
        self.assertEqual(removed,[OLD]);self.assertEqual(len(preserved),2)
        self.assertEqual([call for call in calls if call[:3]==['docker','image','rm']],[['docker','image','rm',OLD]])
    def test_failure_before_commit_restores_both_components(self):
        cfg=self.installed()
        before={name:(Path(item['data_directory'])/'state.json').read_bytes() for name,item in cfg['components'].items()}
        compose=self.manager.compose;failed=[False]
        def fail_once(*args):
            if args==('rm','-f','wg-easy') and not failed[0]:
                failed[0]=True;raise ManagerError('Искусственный отказ удаления контейнера')
            return compose(*args)
        self.manager.compose=fail_once
        with self.assertRaisesRegex(ManagerError,'Искусственный'):self.purge()
        self.assertFalse(self.manager.journal.exists());self.assertTrue((self.manager.root/'backups').exists())
        for name,item in cfg['components'].items():self.assertEqual((Path(item['data_directory'])/'state.json').read_bytes(),before[name])
        self.assertFalse(any(call[:3]==['docker','image','rm'] for call in self.manager.calls))
    def test_multiple_owned_image_tags_removed_without_force(self):
        tags=['ghcr.io/a-baader/wg-dual-easy-awg:0.3.0','wg-dual-easy-awg:local','wg-dual-easy-awg@sha256:'+'a'*64,'vpn-easy-awg@sha256:'+'b'*64];calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['docker','ps','-aq']:return ''
            if args[:3]==['docker','image','inspect']:
                if not tags:return None
                return json.dumps([{'Id':OLD,'Config':{'Labels':{'org.opencontainers.image.source':'https://github.com/a-baader/wg-dual-easy'}},'RepoTags':list(tags)}])
            if args[:3]==['docker','image','rm']:tags.remove(args[-1]);return ''
            return ''
        self.manager.run=run
        self.assertEqual(vpnctl_purge.remove_images(self.manager,[OLD]),([OLD],[]))
        self.assertEqual(tags,[]);self.assertFalse(any('--force' in call for call in calls))
    def test_foreign_image_tag_is_preserved(self):
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            if args[:3]==['docker','image','inspect']:
                return json.dumps([{'Id':OLD,'Config':{'Labels':{'org.opencontainers.image.source':'https://github.com/a-baader/wg-dual-easy'}},'RepoTags':['foreign:keep']}])
            return ''
        self.manager.run=run
        removed,preserved=vpnctl_purge.remove_images(self.manager,[OLD])
        self.assertEqual(removed,[]);self.assertEqual(len(preserved),1)
        self.assertFalse(any(call[:3]==['docker','image','rm'] for call in calls))
    def test_unknown_directory_without_config_is_not_purged(self):
        backup=self.manager.root/'backups';backup.mkdir(parents=True)
        foreign=backup/'foreign.txt';foreign.write_text('чужие данные')
        with self.assertRaisesRegex(ManagerError,'Владение каталогом'):self.purge()
        self.assertEqual(foreign.read_text(),'чужие данные');self.assertEqual(self.manager.calls,[])
    def test_cli_refusal_does_not_write_to_unowned_directory(self):
        foreign=self.manager.root/'backups/foreign.txt';foreign.parent.mkdir(parents=True);foreign.write_text('чужие данные')
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('os.geteuid',return_value=0),mock.patch('vpnctl_manager.global_lock',return_value=nullcontext()),mock.patch('sys.stderr',io.StringIO()):
            self.assertEqual(vpnctl_manager.main(PROJECT,['purge','--yes']),73)
        self.assertFalse((self.manager.root/'operations.jsonl').exists())
        self.assertEqual(foreign.read_text(),'чужие данные')
    def test_shared_system_directory_is_rejected(self):
        self.manager.path=Path('/etc/custom-vpn-test.json');self.manager.root=Path('/etc')
        with self.assertRaisesRegex(ManagerError,'Небезопасный каталог manager'):
            vpnctl_purge.check_paths(self.manager)
    def test_cli_success_does_not_recreate_audit_log(self):
        self.installed()
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('os.geteuid',return_value=0),mock.patch('vpnctl_manager.global_lock',return_value=nullcontext()),mock.patch('sys.stdout',io.StringIO()),mock.patch('sys.stderr',io.StringIO()):
            self.assertEqual(vpnctl_manager.main(PROJECT,['purge','--yes']),0)
        self.assertFalse(self.manager.root.exists())

if __name__=='__main__':unittest.main()

"""Обновление без опций: стабильный релиз, исходники, блокировка и откат."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_manager
import vpnctl_source_update as updater
import vpnctl_boot
import vpnctl_purge
from vpnctl_common import ManagerError
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class SourceUpdate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vpnctl-source-');self.root=Path(self.temp.name)
        self.manager=fixture['FakeManager'](self.root)
        self.config=fixture['config'](self.root);self.manager.register(self.config)
        self.release={'version':'0.2.0','source_commit':'b'*40,'platform':'linux/amd64',
            'images':{name:'ghcr.io/a-baader/wg-dual-easy-'+name+'@sha256:'+'a'*64 for name in ['awg','wg']}}
        self.args=vpnctl_manager.parse_arguments(PROJECT,['update'])[1]
    def tearDown(self):self.temp.cleanup()
    def update(self):
        with mock.patch('vpnctl_boot.fetch_release',return_value=self.release):return self.manager.update(self.args)
    def source(self):
        source=self.root/'new-source';(source/'src').mkdir(parents=True);(source/'lib').mkdir()
        (source/'VERSION').write_text(self.release['version'])
        (source/'src/package.json').write_text(json.dumps({'version':self.release['version']}))
        for name in ['vpnctl','awg-easy-3']:(source/name).write_text('исходники')
        (source/'lib/vpnctl_manager.py').write_text('UPDATE_API_VERSION=1\nclass Manager: pass\n')
        return source
    def test_update_has_no_required_extra_options(self):
        self.assertEqual(self.args.command,'update');self.assertIsNone(self.args.manifest);self.assertEqual(self.args.component,'all')
    def test_no_stable_release_leaves_configuration_unchanged(self):
        before=self.manager.path.read_bytes();self.release=None
        self.assertFalse(self.update()['updated']);self.assertEqual(self.manager.path.read_bytes(),before)
        self.assertFalse(self.manager.journal.exists())
    def test_same_commit_and_images_are_noop(self):
        config=self.manager.config();config['source_commit']=self.release['source_commit']
        for name,item in config['components'].items():item.update(version='0.2.0',image_ref=self.release['images'][name])
        self.manager.write_config(config)
        with mock.patch.object(updater,'stage_source') as stage:self.assertFalse(self.update()['updated'])
        stage.assert_not_called()
    def test_no_downgrade_of_newer_component(self):
        config=self.manager.config();config['components']['wg']['version']='0.3.0';self.manager.write_config(config)
        self.assertFalse(self.update()['updated']);self.assertFalse(self.manager.journal.exists())
    def test_dry_run_does_not_clone_or_write(self):
        self.args.dry_run=True;before=self.manager.path.read_bytes()
        with mock.patch.object(updater,'stage_source') as stage:result=self.update()
        self.assertTrue(result['dry_run']);stage.assert_not_called();self.assertEqual(self.manager.path.read_bytes(),before)
        self.assertFalse(self.manager.journal.exists())
    def test_network_error_preserves_installation(self):
        with mock.patch('vpnctl_boot.fetch_release',side_effect=OSError('ошибка сети')):
            with self.assertRaisesRegex(ManagerError,'Не удалось проверить'):self.manager.update(self.args)
        self.assertFalse(self.manager.journal.exists())
    def test_cloning_failure_clears_only_preparatory_journal(self):
        with mock.patch.object(updater,'stage_source',side_effect=ManagerError('Ошибка загрузки')):
            with self.assertRaisesRegex(ManagerError,'Ошибка загрузки'):self.update()
        self.assertFalse(self.manager.journal.exists());self.assertTrue(self.manager.path.exists())
    def test_preparatory_interruption_is_recoverable(self):
        before=self.manager.path.read_bytes()
        with mock.patch.object(updater,'stage_source',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.update()
        journal=json.loads(self.manager.journal.read_text());self.assertEqual(journal['operation'],'source-update')
        self.manager.rollback(journal);self.assertEqual(self.manager.path.read_bytes(),before);self.assertFalse(self.manager.journal.exists())
    def test_new_manager_gets_exact_manifest_and_same_config(self):
        source=self.source();target=mock.Mock();target.update.return_value={'updated':['awg','wg']}
        def apply(args):
            manifest=json.loads(Path(args.manifest).read_text());self.assertEqual(manifest,self.release)
            self.assertEqual(args.source_commit,self.release['source_commit']);self.manager.journal.unlink()
            return {'updated':['awg','wg']}
        target.update.side_effect=apply
        with mock.patch.object(updater,'stage_source',return_value=source),mock.patch.object(updater,'new_manager',return_value=target):result=self.update()
        self.assertEqual(result['sources_directory'],str(source));self.assertTrue(result['previous_sources_preserved'])
        self.assertEqual(result['source_commit'],self.release['source_commit']);self.assertTrue((PROJECT/'vpnctl').exists())
    def test_load_restores_original_python_modules(self):
        source=self.source();previous=sys.modules['vpnctl_manager']
        with updater.release_namespace(source) as namespace:self.assertEqual(namespace['UPDATE_API_VERSION'],1)
        self.assertIs(sys.modules['vpnctl_manager'],previous)
    def test_cached_sources_must_match_commit_and_version(self):
        source=self.source();cache=self.manager.root/'sources'/('v0.2.0-'+self.release['source_commit']);cache.parent.mkdir();source.rename(cache)
        self.manager.run=lambda args,**kwargs:self.release['source_commit'] if args[-1]=='HEAD' else ''
        self.assertEqual(updater.stage_source(self.manager,self.release),cache)
        (cache/'VERSION').write_text('9.0.0')
        with self.assertRaisesRegex(ManagerError,'Версия исходников'):updater.stage_source(self.manager,self.release)
    def test_changed_cached_sources_are_preserved(self):
        source=self.source();cache=self.manager.root/'sources'/('v0.2.0-'+self.release['source_commit']);cache.parent.mkdir();source.rename(cache)
        self.manager.run=lambda args,**kwargs:self.release['source_commit'] if args[-1]=='HEAD' else ' M vpnctl'
        with self.assertRaisesRegex(ManagerError,'изменены'):updater.stage_source(self.manager,self.release)
        self.assertEqual((cache/'vpnctl').read_text(),'исходники')
    def test_changed_own_system_files_block_source_update(self):
        command=self.manager.system_root/'usr/local/sbin/vpnctl';command.write_text('изменено администратором')
        with self.assertRaisesRegex(ManagerError,'чужой файл'):self.update()
    def test_cli_does_not_require_yes_without_terminal(self):
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('vpnctl_manager.global_lock',return_value=nullcontext()),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=False),mock.patch('vpnctl_boot.fetch_release',return_value=None),mock.patch('sys.stdout',io.StringIO()):
            self.assertEqual(vpnctl_manager.main(PROJECT,['update']),0)
    def test_purge_preserves_cached_active_sources(self):
        self.manager.project=self.manager.root/'sources/v0.2.0'
        vpnctl_purge.check_paths(self.manager,self.manager.config())
    def test_stable_api_can_fetch_current_manifest(self):
        value=self.release;raw=json.dumps(value).encode()
        import hashlib
        def fetch(url):
            if url.endswith('/latest'):return json.dumps({'tag_name':'v0.2.0','draft':False,'prerelease':False}).encode()
            if url.endswith('SHA256SUMS'):return (hashlib.sha256(raw).hexdigest()+'  release-manifest.json\n').encode()
            return raw
        self.assertEqual(vpnctl_boot.fetch_release(fetch=fetch),value)

if __name__=='__main__':unittest.main()

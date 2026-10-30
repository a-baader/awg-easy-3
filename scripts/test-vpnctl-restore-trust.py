"""Подмена image в backup не должна становиться выполнением чужого кода."""
import copy
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_restore_images
import vpnctl_manager
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))


class RestoreTrust(unittest.TestCase):
    def test_default_uses_installed_code_and_explicit_flag_uses_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=fixture['FakeManager'](root);installed=fixture['config'](root)
            original=copy.deepcopy(installed);original['components']['wg']['image']='sha256:'+'f'*64
            manifest={'manager_config':original}
            selected=vpnctl_restore_images.select(manager,manifest,installed,['wg'])
            self.assertEqual(selected['wg']['image'],fixture['OLD'])
            selected=vpnctl_restore_images.select(manager,manifest,installed,['wg'],True)
            self.assertEqual(selected['wg']['image'],'sha256:'+'f'*64)

    def test_fresh_restore_uses_verified_own_release(self):
        manager=type('Manager',(),{'project':PROJECT})()
        release={'version':'0.2.3','images':{'wg':'ghcr.io/a-baader/wg-dual-easy-wg@sha256:'+'a'*64}}
        with mock.patch('vpnctl_restore_images.vpnctl_boot.fetch_manifest',return_value=release):
            selected=vpnctl_restore_images.select(manager,{'manager_config':{}},None,['wg'])
        self.assertEqual(selected['wg']['image'],release['images']['wg'])

    def test_dry_run_never_pulls_or_executes_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=fixture['FakeManager'](root);manager.write_config(fixture['config'](root))
            archive=manager.backup('all')['file'];manager.calls.clear()
            with mock.patch.object(manager,'image',side_effect=AssertionError('Нельзя запускать код')),mock.patch.object(manager,'validate_state_image',side_effect=AssertionError('Нельзя запускать код')):
                result=manager.restore(vpnctl_manager.build_parser().parse_args(['restore','--file',archive,'--component','all','--dry-run']))
            self.assertTrue(result['image_execution_skipped'])
            self.assertFalse(any(call[:2] in (['docker','run'],['docker','pull']) for call in manager.calls))


if __name__=='__main__':unittest.main()

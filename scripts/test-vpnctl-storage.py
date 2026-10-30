"""Retention удаляет только проверенные неактивные ресурсы по явной команде."""
import io
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_manager
import vpnctl_storage
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))


class Storage(unittest.TestCase):
    def test_explicit_cleanup_keeps_new_backups_and_unknown_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=fixture['FakeManager'](root);manager.register(fixture['config'](root))
            archives=[Path(manager.backup('all')['file']) for _ in range(3)]
            for index,path in enumerate(archives):os.utime(path,(time.time()-(50-index)*86400,)*2)
            foreign=manager.root/'backups/foreign.txt';foreign.write_text('Не удалять')
            args=vpnctl_manager.build_parser().parse_args(['storage','cleanup','--keep-backups','1','--older-than-days','30','--dry-run'])
            plan=vpnctl_storage.execute(manager,args)
            self.assertEqual(set(plan['remove']),set(map(str,archives[:2])))
            self.assertTrue(all(path.exists() for path in archives))
            args.dry_run=False
            result=vpnctl_storage.execute(manager,args)
            self.assertEqual(len(result['removed']),2)
            self.assertTrue(archives[-1].exists());self.assertTrue(foreign.exists())

    def test_cleanup_requires_confirmation_and_rejects_negative_retention(self):
        for arguments in [['storage','cleanup'],['storage','cleanup','--yes','--keep-backups','-1']]:
            with mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=False),mock.patch('sys.stderr',io.StringIO()):
                self.assertEqual(vpnctl_manager.main(PROJECT,arguments),64)


if __name__=='__main__':unittest.main()

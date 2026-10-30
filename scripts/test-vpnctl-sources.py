"""Защищённая копия исходников проверяется без изменения /usr/local хоста."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import vpnctl_sources
from vpnctl_common import ManagerError


class Sources(unittest.TestCase):
    def test_snapshot_is_independent_of_checkout_and_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'checkout';project.mkdir()
            (project/'vpnctl').write_text('Исходный CLI')
            (project/'.env').write_text('Не копировать')
            (project/'client.conf').write_text('Приватный пользовательский профиль')
            manager=SimpleNamespace(project=project,system_root=root/'system')
            target=vpnctl_sources.install(manager)
            self.assertFalse((target/'.env').exists())
            self.assertFalse((target/'client.conf').exists())
            (project/'vpnctl').write_text('Изменён непривилегированным владельцем checkout')
            self.assertEqual((target/'vpnctl').read_text(),'Исходный CLI')
            (target/'vpnctl').chmod(0o666)
            with self.assertRaisesRegex(ManagerError,'права'):
                vpnctl_sources.trusted(target/'vpnctl',manager.system_root)

    def test_symlink_in_managed_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);project=root/'checkout';project.mkdir()
            (project/'vpnctl').write_text('CLI')
            system=root/'system';system.mkdir();foreign=root/'foreign';foreign.mkdir()
            (system/'usr').symlink_to(foreign,target_is_directory=True)
            with self.assertRaisesRegex(ManagerError,'владелец'):
                vpnctl_sources.install(SimpleNamespace(project=project,system_root=system))


if __name__=='__main__':unittest.main()

"""Ограничения архива проверяются на малых данных, без tar-bomb и Docker."""
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import vpnctl_backup as backup
from vpnctl_common import ManagerError


class BackupLimits(unittest.TestCase):
    def test_stops_header_iteration_before_complete_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'many.tar.gz'
            with tarfile.open(path,'w:gz') as archive:
                for index in range(100):archive.addfile(tarfile.TarInfo('wg/data/f'+str(index)),io.BytesIO())
            seen=[];original=backup.BoundedTarInfo._proc_member
            def count(item,archive):
                seen.append(item.name);return original(item,archive)
            with mock.patch.object(backup,'MAX_FILES',3),mock.patch.object(backup.BoundedTarInfo,'_proc_member',count):
                with self.assertRaisesRegex(ManagerError,'размер'):backup.verify_archive(path)
            self.assertLess(len(seen),10)

    def test_rejects_oversized_metadata_before_reading_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pax.tar.gz'
            with tarfile.open(path,'w:gz') as archive:
                info=tarfile.TarInfo('wg/data/state.json');info.pax_headers={'comment':'x'*70000}
                archive.addfile(info,io.BytesIO())
            with self.assertRaisesRegex(ManagerError,'metadata'):backup.verify_archive(path)

    def test_compressed_size_is_checked_before_tar_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.tar.gz';path.write_bytes(b'x'*20)
            with mock.patch.object(backup,'MAX_COMPRESSED',10),mock.patch('vpnctl_backup.tarfile.open') as opened:
                with self.assertRaisesRegex(ManagerError,'Сжатый'):backup.verify_archive(path)
                opened.assert_not_called()


if __name__=='__main__':unittest.main()

"""Прогресс удаления, итоговый отчёт и разделение потоков JSON."""
from contextlib import nullcontext
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_manager
from vpnctl_progress import RemovalProgress,removal_progress
from vpnctl_common import ManagerError
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class Terminal(io.StringIO):
    def isatty(self):return True

class Removal(unittest.TestCase):
    def test_heartbeat_only_stderr(self):
        out=io.StringIO();err=Terminal()
        with mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
            progress=RemovalProgress();progress.stage('Удаление образов');progress.heartbeat()
        self.assertEqual(out.getvalue(),'');self.assertIn('[Удаление]',err.getvalue())
        self.assertIn('выполняется, прошло',err.getvalue())
    def test_error_stops_reporter_without_success(self):
        class Manager:
            @removal_progress
            def uninstall(self,args):raise ManagerError('Искусственная ошибка')
        manager=Manager();err=io.StringIO()
        with mock.patch('sys.stderr',err),self.assertRaises(ManagerError):manager.uninstall(SimpleNamespace(dry_run=False))
        self.assertFalse(hasattr(manager,'_removal_progress'))
        self.assertIn('Удаление не завершено',err.getvalue());self.assertNotIn('Удаление завершено',err.getvalue())
    def test_commands_and_json(self):
        for command in (['uninstall','wg','--yes'],['purge','--yes']):
            for mode in ([],['--json'],['--dry-run']):
                with self.subTest(command=command,mode=mode),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);manager=fixture['FakeManager'](root)
                    original=manager.run
                    def run(args,**kwargs):
                        if args[:3] in (['ip','link','show'],['nft','list','table']):return None
                        return original(args,**kwargs)
                    manager.run=run;manager.register(fixture['config'](root))
                    out=io.StringIO();err=io.StringIO()
                    with mock.patch('vpnctl_manager.Manager',return_value=manager),mock.patch('os.geteuid',return_value=0),mock.patch('vpnctl_manager.global_lock',return_value=nullcontext()),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
                        code=vpnctl_manager.main(PROJECT,command+mode)
                    self.assertEqual(code,0)
                    if '--dry-run' in mode:self.assertEqual(err.getvalue(),'')
                    else:
                        text=err.getvalue()
                        for stage in ['Проверка установки','внутреннего backup','контейнера WG','сетевых ресурсов WG','Удаление завершено']:self.assertIn(stage,text)
                        self.assertEqual(text.count('[Удаление] Удаление завершено'),1)
                        if command[0]=='purge':self.assertIn('Удаление внутренних backup',text)
                    if '--json' in mode:
                        result=json.loads(out.getvalue());self.assertIn('removed',result)
                    elif '--dry-run' not in mode:
                        self.assertIn('Итоговый отчёт об удалении:',out.getvalue())
                        self.assertIn('Внутренние backup сохранены:',out.getvalue())
                    self.assertFalse(hasattr(manager,'_removal_progress'))

if __name__=='__main__':unittest.main()

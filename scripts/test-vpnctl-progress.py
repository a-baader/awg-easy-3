"""Этапы установки и периодические сообщения без вывода секретов."""
import io
from pathlib import Path
import runpy
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_progress import InstallationProgress,installation_progress
from vpnctl_manager import parse_arguments
from vpnctl_common import ManagerError
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class Progress(unittest.TestCase):
    def test_stages_and_heartbeat_go_to_stderr_only(self):
        out=io.StringIO();err=io.StringIO()
        with mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
            progress=InstallationProgress();progress.stage('Загрузка образа AWG');progress.heartbeat();progress.stop()
        self.assertEqual(out.getvalue(),'');self.assertIn('Загрузка образа AWG',err.getvalue());self.assertIn('выполняется, прошло',err.getvalue())
    def test_background_heartbeat_runs_and_stops(self):
        progress=InstallationProgress(interval=0.01);called=threading.Event();heartbeat=progress.heartbeat
        def notify():heartbeat();called.set()
        progress.heartbeat=notify
        with mock.patch('sys.stderr',io.StringIO()) as err:
            progress.stage('Ожидание загрузки');progress.start()
            try:self.assertTrue(called.wait(2),'Периодическое сообщение не получено')
            finally:progress.stop()
        self.assertFalse(progress.thread.is_alive());self.assertIn('выполняется, прошло',err.getvalue())
    def test_decorator_stops_thread_on_failure(self):
        class TestManager:
            @installation_progress
            def install(self,args):raise ManagerError('Искусственная ошибка')
        manager=TestManager();err=io.StringIO()
        with mock.patch('sys.stderr',err):
            with self.assertRaises(ManagerError):manager.install(SimpleNamespace(dry_run=False))
        self.assertFalse(hasattr(manager,'_installation_progress'));self.assertIn('Установка не завершена',err.getvalue())
        self.assertNotIn('Установка завершена\n',err.getvalue())
    def test_dry_run_does_not_start_progress(self):
        class TestManager:
            @installation_progress
            def install(self,args):return {'dry_run':True}
        with mock.patch('sys.stderr',io.StringIO()) as err:TestManager().install(SimpleNamespace(dry_run=True))
        self.assertEqual(err.getvalue(),'')
    def test_successful_install_reports_real_stages_without_bootstrap_secrets(self):
        with tempfile.TemporaryDirectory(prefix='vpnctl-progress-') as directory:
            root=Path(directory);manager=fixture['FakeManager'](root)
            def initialize(name,item,config):
                path=Path(item['data_directory']);path.mkdir(parents=True)
                manager.bootstrap_results[name]={'password':'СЕКРЕТ_ПАРОЛЬ','vpn_url':'vpn://СЕКРЕТ_ПРОФИЛЬ'}
            manager.initialize=initialize
            args=parse_arguments(PROJECT,['install','--host','192.0.2.10','--data-root',str(root/'data'),'--awg-image',fixture['OLD'],'--wg-image',fixture['OLD']])[1]
            out=io.StringIO();err=io.StringIO()
            with mock.patch('vpnctl_manager.environment_check'),mock.patch('vpnctl_manager.inspect_conflicts'),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
                result=manager.install(args)
            self.assertTrue(result['installed']);self.assertEqual(out.getvalue(),'')
            for stage in ['Проверка системы','Проверка сети','Подготовка образов','Запуск AWG','Запуск WG','Регистрация vpnctl','Настройка проверки','Установка завершена']:
                self.assertIn(stage,err.getvalue())
            self.assertNotIn('СЕКРЕТ',err.getvalue());self.assertFalse(hasattr(manager,'_installation_progress'))

if __name__=='__main__':unittest.main()

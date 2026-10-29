"""ASCII-индикаторы: реальные состояния, остановка и чистый машинный вывод."""
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from vpnctl_progress import InstallationProgress,RemovalProgress,CommandProgress,command_progress

class Bars(unittest.TestCase):
    def test_activity_is_ascii_and_not_fake_percentage(self):
        progress=InstallationProgress()
        first=progress.bar();second=progress.bar()
        self.assertNotEqual(first,second);first.encode('ascii');self.assertNotIn('%',first)
        progress.finished=True;self.assertEqual(progress.bar(),'[####################] 100%')
    def test_install_and_removal_finish_only_on_success(self):
        for reporter,success,failure in [(InstallationProgress(),'Установка завершена','Установка не завершена'),(RemovalProgress(),'Удаление завершено','Удаление не завершено')]:
            err=io.StringIO()
            with mock.patch('sys.stderr',err):
                reporter.stage('Проверка');reporter.heartbeat();reporter.stage(failure)
                self.assertNotIn('100%',err.getvalue())
                reporter.stage(success)
            self.assertIn('[####################] 100%',err.getvalue())
    def test_all_commands_keep_stdout_clean(self):
        for command in ['status','settings','start','stop','restart','backup','restore','recover','diagnose','update','client','auto-update','reset-password']:
            with self.subTest(command=command):
                out=io.StringIO();err=io.StringIO()
                with mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
                    with command_progress(SimpleNamespace(command=command,dry_run=False,action=None)):print('{"ok":true}')
                self.assertEqual(out.getvalue(),'{"ok":true}\n');self.assertIn('100%',err.getvalue())
    def test_failure_stops_thread_and_never_reaches_full_bar(self):
        err=io.StringIO();reporter=CommandProgress('Проверка')
        with mock.patch('sys.stderr',err),mock.patch('vpnctl_progress.CommandProgress',return_value=reporter):
            with self.assertRaises(ValueError):
                with command_progress(SimpleNamespace(command='update')):raise ValueError('Ошибка проверки')
        self.assertFalse(reporter.thread.is_alive());self.assertNotIn('100%',err.getvalue())
    def test_skip_duplicate_reporters_dry_run_help_and_logs(self):
        for command,dry in [('install',False),('reinstall',False),('uninstall',False),('purge',False),('help',False),('logs',False),('update',True)]:
            with mock.patch('sys.stderr',io.StringIO()) as err:
                with command_progress(SimpleNamespace(command=command,dry_run=dry)):pass
            self.assertEqual(err.getvalue(),'')

if __name__=='__main__':unittest.main()

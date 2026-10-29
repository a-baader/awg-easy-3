"""Тихий Ctrl+C в меню, вложенных вопросах и операциях CLI."""
from contextlib import nullcontext
import io
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
import vpnctl_manager
from vpnctl_progress import installation_progress,removal_progress
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class Interrupt(unittest.TestCase):
    def test_menu_questions_exit_silently(self):
        for answers in [[],['6'],['6','add'],['6','add','wg'],['6','add','wg','Телефон'],['11'],['11','backup.tar.gz']]:
            with self.subTest(answers=answers):
                out=io.StringIO();err=io.StringIO();values=iter(answers)
                def answer():
                    try:return next(values)
                    except StopIteration:
                        out.seek(0);out.truncate();err.seek(0);err.truncate()
                        raise KeyboardInterrupt
                manager=SimpleNamespace(status=lambda:{})
                with mock.patch('vpnctl_manager.Manager',return_value=manager),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',side_effect=answer),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err),mock.patch('vpnctl_manager.emit_result') as emit:
                    code=vpnctl_manager.main(PROJECT,[])
                self.assertEqual(code,130);self.assertEqual(out.getvalue(),'');self.assertEqual(err.getvalue(),'');emit.assert_not_called()
    def test_operation_interrupt_stops_progress_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=fixture['FakeManager'](Path(directory));out=io.StringIO();err=io.StringIO()
            def cancel(*args):
                out.seek(0);out.truncate();err.seek(0);err.truncate();raise KeyboardInterrupt
            with mock.patch('vpnctl_manager.Manager',return_value=manager),mock.patch.object(manager,'status',side_effect=cancel),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
                code=vpnctl_manager.main(PROJECT,['status','--json'])
            self.assertEqual(code,130);self.assertEqual(out.getvalue(),'');self.assertEqual(err.getvalue(),'')
            self.assertFalse(hasattr(manager,'_command_progress'))
    def test_install_and_removal_reporters_are_silent_on_interrupt(self):
        for decorator,attribute in [(installation_progress,'_installation_progress'),(removal_progress,'_removal_progress')]:
            manager=SimpleNamespace();out=io.StringIO();err=io.StringIO()
            @decorator
            def operation(manager,args):raise KeyboardInterrupt
            with mock.patch('sys.stdout',out),mock.patch('sys.stderr',err),self.assertRaises(KeyboardInterrupt):operation(manager,SimpleNamespace(dry_run=False))
            self.assertEqual(out.getvalue(),'');self.assertEqual(err.getvalue(),'');self.assertFalse(hasattr(manager,attribute))
    def test_actual_sigint_has_no_traceback(self):
        script="""
import sys,time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,sys.argv[1]+'/lib')
import vpnctl_manager

def wait():
    print('READY',flush=True)
    time.sleep(60)
with mock.patch('vpnctl_manager.Manager',return_value=SimpleNamespace(status=lambda:{})),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',side_effect=wait):
    raise SystemExit(vpnctl_manager.main(Path(sys.argv[1]),[]))
"""
        process=subprocess.Popen([sys.executable,'-u','-c',script,str(PROJECT)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            while True:
                line=process.stdout.readline()
                self.assertTrue(line,'Процесс завершился до вопроса меню')
                if line.strip()=='READY':break
            process.send_signal(signal.SIGINT)
            out,err=process.communicate(timeout=5)
            self.assertEqual(process.returncode,130);self.assertEqual(out,'');self.assertEqual(err,'Действие: ')
        finally:
            if process.poll() is None:process.kill();process.wait()

if __name__=='__main__':unittest.main()

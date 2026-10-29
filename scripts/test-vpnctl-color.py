"""Проверки цветов терминала и чистоты машинного вывода."""
import io
import json
import os
from pathlib import Path
import pty
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_terminal import paint,message
from vpnctl_output import emit_result,format_human,result_path
from vpnctl_progress import InstallationProgress
from vpnctl_manager import parse_arguments,ask
import vpnctl_manager
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class Terminal(io.StringIO):
    def isatty(self):return True

class Color(unittest.TestCase):
    def setUp(self):
        self.env=mock.patch.dict(os.environ,{'TERM':'xterm'},clear=True);self.env.start()
        self.addCleanup(self.env.stop)
    def test_redirect_is_plain(self):
        with mock.patch('sys.stdout',io.StringIO()):self.assertEqual(paint('Текст'),'Текст')
    def test_tty_and_reset(self):
        self.assertEqual(paint('Текст','error',Terminal()),'\033[31mТекст\033[0m')
    def test_no_color_and_dumb(self):
        for env in [{'NO_COLOR':''},{'NO_COLOR':'1'},{'TERM':'dumb'}]:
            with self.subTest(env=env),mock.patch.dict(os.environ,env):
                self.assertEqual(paint('Текст','error',Terminal()),'Текст')
    def test_streams_are_independent(self):
        out=io.StringIO();err=Terminal()
        with mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
            message('Ошибка: проверка',file=sys.stderr);message('Результат')
        self.assertIn('\033[31m',err.getvalue());self.assertNotIn('\033',out.getvalue())
    def test_human_nested_status(self):
        with mock.patch('sys.stdout',Terminal()):
            text=format_human({'components':{'wg':{'running':True,'health':'unhealthy'}}})
        self.assertIn('\033[36mWG\033[0m',text)
        self.assertIn('\033[32mда\033[0m',text)
        self.assertIn('\033[31munhealthy\033[0m',text)
    def test_help_subprocess_real_pty_and_redirect(self):
        for args in [['-help'],['help'],['install','--help']]:
            with self.subTest(args=args):
                plain=subprocess.run([str(PROJECT/'vpnctl'),*args],capture_output=True,check=True).stdout
                self.assertNotIn(b'\033',plain)
                master,slave=pty.openpty()
                try:
                    result=subprocess.run([str(PROJECT/'vpnctl'),*args],stdout=slave,stderr=subprocess.PIPE)
                    os.close(slave);slave=None
                    chunks=[]
                    while True:
                        try:chunk=os.read(master,65536)
                        except OSError:break
                        if not chunk:break
                        chunks.append(chunk)
                    colored=b''.join(chunks)
                finally:
                    os.close(master)
                    if slave is not None:os.close(slave)
                self.assertEqual(result.returncode,0);self.assertIn(b'\033[1;36m',colored)
                self.assertEqual(colored.count('Использование:'.encode()),1)
    def test_parser_error_is_red_on_stderr(self):
        err=Terminal()
        with mock.patch('sys.stderr',err),self.assertRaises(SystemExit):parse_arguments(PROJECT,['unknown-command'])
        self.assertIn('\033[31mОшибка',err.getvalue())
    def test_progress_success_error_and_prompt(self):
        err=Terminal()
        with mock.patch('sys.stderr',err),mock.patch('builtins.input',return_value='да'):
            progress=InstallationProgress();progress.stage('Проверка');progress.heartbeat()
            progress.stage('Установка завершена');progress.stage('Установка не завершена')
            self.assertEqual(ask('Продолжить? '),'да')
        text=err.getvalue()
        for code in ['36','32','31','1;33']:self.assertIn('\033['+code+'m',text)
    def test_report_and_json_stay_plain_even_on_tty(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=fixture['FakeManager'](Path(directory));manager.write_config(fixture['config'](Path(directory)))
            result={'installed':True};out=Terminal()
            args=parse_arguments(PROJECT,['status'])[1]
            with mock.patch('sys.stdout',out):emit_result(manager,args,result)
            self.assertIn('\033',out.getvalue())
            report=result_path(manager,args).read_text()
            self.assertNotIn('\033',report);self.assertNotIn('\\u001b',report)
            self.assertEqual(json.loads(report)['result'],result)
            out=Terminal();args=parse_arguments(PROJECT,['status','--json'])[1]
            with mock.patch('sys.stdout',out):emit_result(manager,args,result)
            self.assertNotIn('\033',out.getvalue());self.assertEqual(json.loads(out.getvalue()),result)
    def test_raw_export_stays_exact_on_tty(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=fixture['FakeManager'](Path(directory));manager.write_config(fixture['config'](Path(directory)))
            out=Terminal();profile='[Interface]\nAddress = 10.9.0.2/32\n'
            with mock.patch('vpnctl_manager.Manager',return_value=manager),mock.patch('os.geteuid',return_value=0),mock.patch.object(manager,'rpc',return_value={'value':profile}),mock.patch('sys.stdout',out):
                code=vpnctl_manager.main(PROJECT,['client','export','--component','wg','--name','test','--format','conf'])
            self.assertEqual(code,0);self.assertEqual(out.getvalue(),profile)

if __name__=='__main__':unittest.main()

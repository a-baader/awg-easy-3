"""Текстовый вывод по умолчанию, явный JSON и отчёты без секретов."""
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import parse_arguments,confirmation
import vpnctl_manager
from vpnctl_common import ManagerError
from vpnctl_output import emit_result,format_human,public_value,result_path,write_result
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))

class Output(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vpnctl-output-');self.root=Path(self.temp.name)
        self.manager=fixture['FakeManager'](self.root)
        cfg=fixture['config'](self.root);self.manager.write_config(cfg)
    def tearDown(self):self.temp.cleanup()
    def args(self,*values):return parse_arguments(PROJECT,list(values))[1]
    def test_readable_status_without_json_syntax(self):
        text=format_human({'installed':True,'components':{'awg':{'running':True,'health':'healthy'}}})
        for word in ['Установлено: да','Сервисы:','AWG:','Запущен: да']:self.assertIn(word,text)
        for symbol in ['{','}','"']:self.assertNotIn(symbol,text)
    def test_default_prints_text_and_writes_private_json_file(self):
        args=self.args('status');out=io.StringIO()
        with mock.patch('sys.stdout',out):self.assertEqual(emit_result(self.manager,args,{'installed':True}),0)
        self.assertIn('Состояние сервисов:',out.getvalue());self.assertIn('Установлено: да',out.getvalue())
        path=result_path(self.manager,args);self.assertTrue(path.is_file())
        report=json.loads(path.read_text());self.assertEqual(report['result'],{'installed':True})
        self.assertEqual(path.stat().st_mode&0o777,0o600)
    def test_explicit_json_is_one_document_and_no_report_is_written(self):
        args=self.args('status','--json');out=io.StringIO()
        with mock.patch('sys.stdout',out):emit_result(self.manager,args,{'installed':True})
        self.assertEqual(json.loads(out.getvalue()),{'installed':True})
        self.assertFalse(result_path(self.manager,args).exists())
    def test_install_report_has_panels_and_paths_but_no_secrets(self):
        args=self.args('install');out=io.StringIO()
        self.manager.bootstrap_results={'wg':{'password':'СЕКРЕТ_ПАРОЛЬ','vpn_url':'vpn://СЕКРЕТ_ССЫЛКА',
            'panel_url':'http://10.9.0.1:51825','profile_file':'/etc/vpn-easy-dual/bootstrap/wg.conf'}}
        with mock.patch('sys.stdout',out):emit_result(self.manager,args,{'installed':True})
        self.assertIn('СЕКРЕТ_ПАРОЛЬ',out.getvalue());self.assertIn('vpn://СЕКРЕТ_ССЫЛКА',out.getvalue())
        path=result_path(self.manager,args);self.assertEqual(path.name,'wg-dual-easy-install-result.json')
        text=path.read_text();self.assertNotIn('СЕКРЕТ',text);self.assertNotIn('vpn://',text)
        report=json.loads(text);self.assertEqual(report['panels']['wg']['panel_url'],'http://10.9.0.1:51825')
        self.assertEqual(report['panels']['wg']['profile_file'],'/etc/vpn-easy-dual/bootstrap/wg.conf')
    def test_filter_removes_nested_secrets_and_profile_content(self):
        value={'name':'Телефон','data':[{'private_key':'СКРЫТЬ','presharedKey':'СКРЫТЬ','password':'СКРЫТЬ',
             'secret':'СКРЫТЬ','token':'СКРЫТЬ','nativeConfig':'СКРЫТЬ','vpn_url':'СКРЫТЬ'}],
             'message':'Импорт vpn://СКРЫТЬ','profile':'[Interface]\nPrivateKey = СКРЫТЬ'}
        text=json.dumps(public_value(value),ensure_ascii=False)
        self.assertNotIn('СКРЫТЬ',text);self.assertNotIn('vpn://',text);self.assertIn('Телефон',text)
    def test_client_actions_and_reinstall_have_consistent_paths(self):
        for values,name in [(['client','list'],'client-list'),(['auto-update','status'],'auto-update-status'),(['reinstall'],'install'),(['purge'],'purge')]:
            self.assertEqual(result_path(self.manager,self.args(*values)).name,'wg-dual-easy-'+name+'-result.json')
    def test_foreign_report_and_symlink_are_not_overwritten(self):
        path=self.root/'foreign';path.write_text('чужие данные')
        with self.assertRaisesRegex(ManagerError,'не принадлежит'):write_result({},path)
        link=self.root/'link';link.symlink_to(path)
        with self.assertRaisesRegex(ManagerError,'символьной'):write_result({},link)
        self.assertEqual(path.read_text(),'чужие данные')
    def test_confirmation_prompt_does_not_pollute_stdout(self):
        args=self.args('uninstall','--json');out=io.StringIO();err=io.StringIO()
        with mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',return_value='да'),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
            confirmation(args,'Удаление')
        self.assertEqual(out.getvalue(),'');self.assertIn('Продолжить?',err.getvalue())
    def test_keep_existing_install_with_json_returns_one_document(self):
        out=io.StringIO();err=io.StringIO()
        with mock.patch('vpnctl_manager.Manager',return_value=self.manager),mock.patch('os.geteuid',return_value=0),mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',return_value='1'),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
            code=vpnctl_manager.main(PROJECT,['install','--json'])
        self.assertEqual(code,0);self.assertTrue(json.loads(out.getvalue())['unchanged'])
        self.assertIn('Установка уже существует',err.getvalue())
    def test_report_failure_does_not_hide_one_time_password(self):
        self.manager.bootstrap_results={'wg':{'password':'ОДНОКРАТНЫЙ_ПАРОЛЬ','panel_url':'http://10.9.0.1:51825','profile_file':'/tmp/wg.conf'}}
        out=io.StringIO()
        with mock.patch('sys.stdout',out),mock.patch('vpnctl_output.write_result',side_effect=OSError('Ошибка диска')):
            with self.assertRaises(OSError):emit_result(self.manager,self.args('install'),{'installed':True})
        self.assertIn('ОДНОКРАТНЫЙ_ПАРОЛЬ',out.getvalue())
    def test_own_report_can_be_updated_and_permissions_restricted(self):
        path=self.root/'report.json';report={'application':'wg-dual-easy','report_version':1,'result':{}}
        write_result(report,path);path.chmod(0o644);report['result']={'installed':True};write_result(report,path)
        self.assertTrue(json.loads(path.read_text())['result']['installed']);self.assertEqual(path.stat().st_mode&0o777,0o600)

if __name__=='__main__':unittest.main()

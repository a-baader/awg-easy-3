"""Проверяет договорённости Python/JS/shell без установки и сетевых мутаций."""
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import build_parser,parse_arguments
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))


class ContractConsistency(unittest.TestCase):
    def test_python_and_javascript_identify_same_owned_instances(self):
        defaults=json.loads(subprocess.check_output(['node','-e',"console.log(JSON.stringify(require('./src/lib/InstanceConfig').DEFAULTS))"],cwd=PROJECT,text=True))
        with tempfile.TemporaryDirectory() as directory,mock.patch.dict(os.environ,{},clear=True):
            manager=fixture['FakeManager'](Path(directory))
            # CLI выбирает случайный AWG UDP по умолчанию; явный override
            # позволяет проверить общий контракт port/backend без случайности.
            args=parse_arguments(PROJECT,['install','--host','192.0.2.10','--port',str(defaults['amneziawg']['listenPort']),'--data-root',directory])[1]
            plan=manager.plan_install(args)
        fields={'backend':'backend','interface':'interfaceName','table':'tableName','container':'containerName',
                'network4':'ipv4Subnet','address4':'address4','panel_port':'panelPort','udp_port':'listenPort'}
        for item in plan['components'].values():
            for python,javascript in fields.items():self.assertEqual(item[python],defaults[item['backend']][javascript])

    def test_shell_and_python_agree_on_port_boundaries(self):
        for value in ['1','65535','0','-1','65536','не-порт']:
            with self.subTest(value=value),mock.patch.dict(os.environ,{},clear=True),mock.patch('sys.stderr',io.StringIO()):
                try:parse_arguments(PROJECT,['install','--port',value]);accepted=True
                except SystemExit:accepted=False
                shell=subprocess.run(['/bin/sh',str(PROJECT/'lib/vpnctl-install-validate.sh'),'install','--port',value],env={},capture_output=True,text=True)
                self.assertEqual(shell.returncode==0,accepted)

    def test_pre_python_help_lists_every_python_command(self):
        parser=build_parser()
        commands=parser._subparsers._group_actions[0].choices
        result=subprocess.check_output(['/bin/sh',str(PROJECT/'lib/vpnctl-install-validate.sh'),'--help'],text=True)
        for name in commands:self.assertRegex(result,r'(?m)^  '+name+r'\s')


if __name__=='__main__':unittest.main()

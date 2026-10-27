"""Контракт оригинальной установки и границы удаления Dual, без изменения хоста."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import Manager,parse_arguments,confirmation
from vpnctl_common import ManagerError,atomic_json,atomic_write
import vpnctl_boot
fixture=runpy.run_path(str(PROJECT/'scripts/test-vpnctl.py'))
FakeManager=fixture['FakeManager'];make_config=fixture['config']

class Contract(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vpnctl-contract-');self.root=Path(self.temp.name)
        self.manager=FakeManager(self.root)
        original=self.manager.run
        def run(args,**kwargs):
            if args[:3]==['ip','link','show'] or args[:3]==['nft','list','table']:return None
            return original(args,**kwargs)
        self.manager.run=run
    def tearDown(self):self.temp.cleanup()
    def args(self,values):return parse_arguments(PROJECT,values)[1]
    def installed(self):
        cfg=make_config(self.root);self.manager.register(cfg);return self.manager.config()
    def test_original_aliases_environment_and_cli_priority(self):
        with mock.patch.dict(os.environ,{'AWG_HOST':'env.example','AWG_PORT':'39516','AWG_PANEL_PORT':'51831','AWG_LANG':'ru'},clear=True):
            args=self.args(['--reinstall','--host','cli.example','--port','39517'])
        self.assertEqual((args.command,args.component,args.host,args.awg_port,args.awg_panel_port,args.lang),('reinstall','all','cli.example',39517,51831,'ru'))
        self.assertEqual(self.args(['--uninstall']).command,'uninstall')
        self.assertEqual(self.args(['install']).wg_image,'release://'+(PROJECT/'VERSION').read_text().strip()+'/wg')
        self.assertEqual(self.args(['install','--build']).awg_image,'vpn-easy-awg:dual-dev')
    def test_bad_parameters_are_rejected_before_dependencies(self):
        for arguments in [['--port','0'],['--uninstall','--reinstall'],['--lang','bad'],['--unknown']]:
            result=subprocess.run([str(PROJECT/'vpnctl'),'install',*arguments],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(result.returncode,64,result.stderr)
            self.assertIn('Ошибка параметров',result.stderr)
            self.assertFalse(self.manager.path.exists())
        with mock.patch.dict(os.environ,{'AWG_PORT':'65536'}),mock.patch('sys.stderr',io.StringIO()):
            with self.assertRaises(SystemExit) as error:self.args(['install'])
        self.assertEqual(error.exception.code,64)
    def test_endpoint_uses_route_get_source_including_private_address_like_original(self):
        original=self.manager.run
        def run(args,**kwargs):
            if args==['ip','-j','-4','route','get','1.1.1.1']:
                return json.dumps([{'dev':'eth1','prefsrc':'172.16.0.2'}])
            return original(args,**kwargs)
        self.manager.run=run
        args=self.args(['install','--data-root',str(self.root/'new-data')])
        with mock.patch.dict(os.environ,{},clear=True):plan=self.manager.plan_install(args)
        for item in plan['components'].values():
            self.assertEqual(item['endpoint'],'172.16.0.2');self.assertEqual(item['wan_interface'],'eth1')
    def test_default_release_resolution_is_verified_and_cached_for_both_images(self):
        version=(PROJECT/'VERSION').read_text().strip()
        release={'version':version,'images':{name:'ghcr.io/a-baader/awg-easy-3-'+name+'@sha256:'+'b'*64 for name in ['awg','wg']}}
        with mock.patch('vpnctl_boot.fetch_manifest',return_value=release) as fetch:
            for name in ['awg','wg']:
                self.assertEqual(Manager.resolve_image_reference(self.manager,'release://'+version+'/'+name),release['images'][name])
        fetch.assert_called_once_with(version)
        self.assertEqual(json.loads((self.manager.root/'releases'/('v'+version+'.json')).read_text()),release)
    def test_failed_default_manifest_does_not_start_containers(self):
        with mock.patch('vpnctl_boot.fetch_manifest',side_effect=ManagerError('Неверный manifest')):
            with self.assertRaisesRegex(ManagerError,'Не удалось проверить'):
                Manager.resolve_image_reference(self.manager,'release://0.2.0-rc.2/wg')
        self.assertFalse(self.manager.calls);self.assertFalse(self.manager.path.exists())
    def test_update_refreshes_owned_installed_launcher(self):
        cfg=self.installed();command=self.manager.system_root/'usr/local/sbin/vpnctl'
        command.write_text('#!/bin/sh\n# Прежняя версия\n')
        cfg['manager_files'][str(command)]=hashlib.sha256(command.read_bytes()).hexdigest();self.manager.write_config(cfg)
        manifest=self.root/'update.json';atomic_json(manifest,{'version':(PROJECT/'VERSION').read_text().strip(),'images':{'wg':fixture['OLD']}})
        with mock.patch('sys.stderr',io.StringIO()):self.manager.update(self.args(['update','wg','--manifest',str(manifest),'--yes']))
        self.assertEqual(command.read_bytes(),(PROJECT/'vpnctl').read_bytes())
    def test_update_does_not_overwrite_foreign_installed_launcher(self):
        cfg=self.installed();command=self.manager.system_root/'usr/local/sbin/vpnctl'
        command.write_text('Изменено администратором')
        manifest=self.root/'update.json';atomic_json(manifest,{'version':(PROJECT/'VERSION').read_text().strip(),'images':{'wg':fixture['OLD']}})
        before=(Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes()
        with self.assertRaisesRegex(ManagerError,'чужой файл'):
            self.manager.update(self.args(['update','wg','--manifest',str(manifest),'--yes']))
        self.assertEqual(command.read_text(),'Изменено администратором')
        self.assertEqual((Path(cfg['components']['wg']['data_directory'])/'state.json').read_bytes(),before)
        self.assertFalse(any(call[:2]==['compose','stop'] for call in self.manager.calls))
    def test_empty_environment_port_is_invalid(self):
        with mock.patch.dict(os.environ,{'AWG_PORT':''}),mock.patch('sys.stderr',io.StringIO()):
            with self.assertRaises(SystemExit):self.args(['install'])
    def test_launcher_and_symlink_are_independent_of_cwd(self):
        link=self.root/'vpnctl-link';link.symlink_to(PROJECT/'vpnctl')
        for launcher in [PROJECT/'vpnctl',link,PROJECT/'install.sh']:
            result=subprocess.run([str(launcher),*(['install'] if launcher.name!='install.sh' else []),'--help'],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('--panel-port',result.stdout)
        self.assertEqual(self.args(['install','--data-root','relative']).data_root,str(PROJECT/'relative'))
    def test_destructive_confirmation_is_explicit(self):
        args=SimpleNamespace(yes=False,dry_run=False)
        with mock.patch('sys.stdin.isatty',return_value=False):
            with self.assertRaisesRegex(ManagerError,'Требуется подтверждение'):confirmation(args,'Удаление')
        with mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',side_effect=['непонятно','нет']),mock.patch('sys.stderr',io.StringIO()):
            with self.assertRaisesRegex(ManagerError,'отменена'):confirmation(args,'Удаление')
        with mock.patch('sys.stdin.isatty',return_value=True),mock.patch('builtins.input',side_effect=EOFError):
            with self.assertRaisesRegex(ManagerError,'отменена'):confirmation(args,'Удаление')
    def test_port_retry_wrap_and_all_udp_sockets(self):
        self.manager.busy.add(('udp',65535));self.manager.interactive=True
        with mock.patch('builtins.input',side_effect=['bad','65535','']):
            with mock.patch('sys.stderr',io.StringIO()):self.assertEqual(self.manager.choose_port('udp',65535,False),1024)
        self.assertTrue(any(call[0]=='ss' and call[1]=='-Huan' for call in self.manager.calls))
    def test_uninstall_last_removes_command_keeps_backups_foreign_files_forwarding(self):
        cfg=self.installed();command=self.manager.system_root/'usr/local/sbin/vpnctl'
        foreign=self.root/'foreign';foreign.write_text('чужие данные')
        cfg['manager_files'][str(foreign)]=hashlib.sha256(foreign.read_bytes()).hexdigest();self.manager.write_config(cfg)
        result=self.manager.uninstall(self.args(['uninstall','all','--yes']))
        self.assertTrue(result['vpnctl_removed']);self.assertFalse(command.exists());self.assertFalse(self.manager.path.exists())
        self.assertTrue(Path(result['snapshot']).is_file());self.assertTrue(foreign.exists())
        self.assertFalse(any(Path(item['data_directory']).exists() for item in cfg['components'].values()))
        self.assertFalse(any(call[0]=='sysctl' or 'prune' in call or 'down' in call for call in self.manager.calls))
    def test_partial_uninstall_keeps_command_other_state_and_backups(self):
        cfg=self.installed();before=(Path(cfg['components']['awg']['data_directory'])/'state.json').read_bytes()
        result=self.manager.uninstall(self.args(['uninstall','wg','--yes']))
        self.assertFalse(result['vpnctl_removed']);self.assertTrue((self.manager.system_root/'usr/local/sbin/vpnctl').exists())
        self.assertEqual((Path(cfg['components']['awg']['data_directory'])/'state.json').read_bytes(),before)
        self.assertTrue(Path(result['snapshot']).exists());self.assertEqual(set(self.manager.config()['components']),{'awg'})
    def test_modified_command_and_symlink_are_not_deleted(self):
        cfg=self.installed();command=self.manager.system_root/'usr/local/sbin/vpnctl';command.write_text('чужая команда')
        compat=self.manager.system_root/'usr/local/sbin/awg-easy-3';compat.unlink();compat.symlink_to(command)
        self.manager.uninstall(self.args(['uninstall','--yes']))
        self.assertEqual(command.read_text(),'чужая команда');self.assertTrue(compat.is_symlink())
    def initialize(self,name,item,config):
        directory=Path(item['data_directory']);directory.mkdir(mode=0o700)
        atomic_json(directory/'state.json',{'version':3,'server':{'backend':item['backend']},'new':'новые ключи'})
        atomic_write(self.manager.root/'bootstrap'/(name+'.conf'),'новый профиль')
    def test_reinstall_replaces_selected_data_keeps_other_and_old_snapshot(self):
        cfg=self.installed();awg=Path(cfg['components']['awg']['data_directory'])/'state.json';wg=Path(cfg['components']['wg']['data_directory'])/'state.json'
        before_awg=awg.read_bytes();before_wg=wg.read_bytes();self.manager.initialize=self.initialize
        args=self.args(['reinstall','wg','--host','192.0.2.10','--yes'])
        with mock.patch('vpnctl_manager.environment_check'):result=self.manager.reinstall(args)
        self.assertEqual(awg.read_bytes(),before_awg);self.assertNotEqual(wg.read_bytes(),before_wg)
        self.assertTrue(Path(result['snapshot']).exists());self.assertFalse(self.manager.journal.exists())
        self.assertFalse(any(call[0]=='sysctl' for call in self.manager.calls))
    def test_full_reinstall_resets_project_settings_and_migration_metadata(self):
        cfg=self.installed();cfg['language']='ru';cfg['auto_check']=False;cfg['legacy_origin']={'тест':'прежняя миграция'}
        self.manager.write_config(cfg);self.manager.initialize=self.initialize
        args=self.args(['reinstall','all','--host','192.0.2.10','--yes'])
        with mock.patch('vpnctl_manager.environment_check'),mock.patch('sys.stderr',io.StringIO()):self.manager.reinstall(args)
        current=self.manager.config()
        self.assertEqual(current['language'],'en');self.assertTrue(current['auto_check']);self.assertNotIn('legacy_origin',current)
        for item in current['components'].values():self.assertEqual(item['version'],(PROJECT/'VERSION').read_text().strip())
    def test_partial_update_tracks_old_backend_for_next_boot_check(self):
        cfg=self.installed();cfg['auto_check']=True;self.manager.write_config(cfg)
        release=self.root/'release.json';atomic_json(release,{'version':'0.2.0','images':{'wg':fixture['OLD']}})
        self.manager.update(self.args(['update','wg','--manifest',str(release),'--yes']))
        current=self.manager.config()
        self.assertEqual(current['components']['awg']['version'],'0.1.6')
        self.assertEqual(current['components']['wg']['version'],'0.2.0')
        with mock.patch('vpnctl_boot.fetch_release',return_value=None) as fetch:
            vpnctl_boot.run(self.manager)
        fetch.assert_called_once_with('0.1.6')
    def test_boot_update_does_not_downgrade_newer_backend(self):
        cfg=self.installed();cfg['auto_check']=True
        cfg['components']['awg']['version']='0.2.0';cfg['components']['wg']['version']='0.3.0-rc.1'
        self.manager.write_config(cfg)
        with mock.patch('vpnctl_boot.fetch_release',return_value={'version':'0.2.1','images':{}}),mock.patch.object(self.manager,'update',return_value={'updated':['awg']}) as update:
            self.assertEqual(vpnctl_boot.run(self.manager),{'updated':['awg']})
        self.assertEqual(update.call_args.args[0].component,'awg')
    def test_failed_reinstall_restores_state_and_bootstrap(self):
        cfg=self.installed();target=Path(cfg['components']['wg']['data_directory'])/'state.json';before=target.read_bytes()
        profile=self.manager.root/'bootstrap/wg.conf';atomic_write(profile,'старый профиль')
        self.manager.initialize=self.initialize
        args=self.args(['reinstall','wg','--host','192.0.2.10','--yes'])
        self.manager.fail_image=args.wg_image
        with mock.patch('vpnctl_manager.environment_check'):
            with self.assertRaisesRegex(ManagerError,'Искусственный'):self.manager.reinstall(args)
        self.assertEqual(target.read_bytes(),before);self.assertEqual(profile.read_text(),'старый профиль')
        self.assertFalse(self.manager.journal.exists())
    def test_committed_uninstall_recovery_does_not_restore_deleted_clients(self):
        cfg=self.installed();original=self.manager.finish_uninstall
        with mock.patch.object(self.manager,'finish_uninstall',side_effect=ManagerError('После фиксации',75)):
            with self.assertRaises(ManagerError):self.manager.uninstall(self.args(['uninstall','--yes']))
        journal=json.loads(self.manager.journal.read_text());self.assertEqual(journal['phase'],'committed')
        self.manager.rollback(journal)
        self.assertFalse(self.manager.path.exists());self.assertFalse(self.manager.journal.exists())
        self.assertFalse(any(Path(item['data_directory']).exists() for item in cfg['components'].values()))
    def test_boot_units_owned_enable_disable_and_last_uninstall(self):
        (self.manager.system_root/'run/systemd/system').mkdir(parents=True)
        with mock.patch('vpnctl_boot.shutil.which',return_value='/usr/bin/systemctl'):
            cfg=self.installed();cfg['auto_check']=True;self.manager.write_config(cfg)
            self.assertTrue(vpnctl_boot.configure(self.manager,True)['enabled'])
            timer=self.manager.system_root/'etc/systemd/system/vpn-easy-dual-update.timer'
            self.assertIn('OnActiveSec=5min',timer.read_text());self.assertIn('Persistent=false',timer.read_text())
            self.manager.uninstall(self.args(['uninstall','--yes']))
            self.assertFalse(timer.exists());self.assertIn(['systemctl','disable','--now','vpn-easy-dual-update.timer'],self.manager.calls)
    def test_restore_after_full_uninstall_uses_archived_custom_paths(self):
        cfg=self.installed();before={name:(Path(item['data_directory'])/'state.json').read_bytes() for name,item in cfg['components'].items()}
        backup=self.manager.backup('all')['file']
        self.manager.uninstall(self.args(['uninstall','--yes']))
        self.manager.restore(self.args(['restore','--file',backup,'--component','all','--yes']))
        for name,item in self.manager.config()['components'].items():
            self.assertEqual(item['data_directory'],cfg['components'][name]['data_directory'])
            self.assertEqual((Path(item['data_directory'])/'state.json').read_bytes(),before[name])
    def test_openrc_files_and_enable_disable_calls(self):
        (self.manager.system_root/'etc/init.d').mkdir(parents=True)
        with mock.patch('vpnctl_boot.shutil.which',return_value='/sbin/openrc-run'):
            self.installed()
            self.assertTrue(vpnctl_boot.configure(self.manager,True)['enabled'])
            for path in vpnctl_boot.files(self.manager.system_root,'openrc'):
                result=subprocess.run(['sh','-n',str(path)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            original=self.manager.run
            def run(args,**kwargs):
                if args[:2]==['rc-update','del']:
                    self.assertFalse(kwargs.get('check',True))
                    return None
                return original(args,**kwargs)
            self.manager.run=run
            vpnctl_boot.configure(self.manager,False)
            vpnctl_boot.configure(self.manager,False)
        self.assertIn(['rc-update','add','vpn-easy-dual-update','default'],self.manager.calls)
        self.assertIn(['rc-service','vpn-easy-dual-update','stop'],self.manager.calls)
    def test_modified_boot_unit_is_preserved_without_stopping_foreign_service(self):
        (self.manager.system_root/'run/systemd/system').mkdir(parents=True)
        with mock.patch('vpnctl_boot.shutil.which',return_value='/usr/bin/systemctl'):
            self.installed();unit=self.manager.system_root/'etc/systemd/system/vpn-easy-dual-update.service'
            unit.write_text('Чужая служба')
            with mock.patch('sys.stderr',io.StringIO()):self.manager.uninstall(self.args(['uninstall','--yes']))
        self.assertEqual(unit.read_text(),'Чужая служба')
        self.assertFalse(any(call[0]=='systemctl' for call in self.manager.calls))
    def test_boot_status_does_not_claim_enabled_on_unsupported_init(self):
        cfg=self.installed();cfg['auto_check']=True;self.manager.write_config(cfg)
        result=vpnctl_boot.status(self.manager)
        self.assertTrue(result['requested']);self.assertFalse(result['enabled'])
    def test_stable_manifest_verification_rc_skip_and_same_version_rc_upgrade(self):
        release={'version':'0.2.0','images':{name:'ghcr.io/a-baader/awg-easy-3-'+name+'@sha256:'+'a'*64 for name in ['awg','wg']},'source_commit':'b'*40,'platform':'linux/amd64'}
        raw=json.dumps(release).encode();api=json.dumps({'tag_name':'v0.2.0','draft':False,'prerelease':False}).encode()
        def fetch(url):
            if url.endswith('/latest'):return api
            if url.endswith('SHA256SUMS'):return (hashlib.sha256(raw).hexdigest()+'  release-manifest.json\n').encode()
            return raw
        self.assertEqual(vpnctl_boot.fetch_release('0.2.0-rc.1',fetch),release)
        self.assertIsNone(vpnctl_boot.fetch_release('0.2.0',fetch))
        release['images']['wg']='foreign:latest';raw=json.dumps(release).encode()
        with self.assertRaisesRegex(ManagerError,'неподходящие'):vpnctl_boot.fetch_release('0.1.6',fetch)
        api=json.dumps({'tag_name':'v0.3.0-rc.1','prerelease':True}).encode()
        with self.assertRaisesRegex(ManagerError,'стабильный'):vpnctl_boot.fetch_release('0.2.0',fetch)

if __name__=='__main__':unittest.main()

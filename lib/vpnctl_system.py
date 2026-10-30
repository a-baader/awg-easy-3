"""Системные файлы, защищённые исходники и boot-интеграция приложения."""
import base64
import hashlib
import json
import sys
from pathlib import Path
from vpnctl_common import ManagerError, atomic_write
from vpnctl_terminal import message as terminal_message
import vpnctl_boot

class SystemIntegration:
    def system_files(self):
        project=getattr(self,'installed_project',self.project)
        return {
            self.system_root/'etc/sysctl.d/99-vpn-easy-dual.conf':('net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n',0o644),
            self.system_root/'etc/modules-load.d/vpn-easy-dual.conf':('tun\n',0o644),
            self.system_root/'etc/vpn-easy-dual-install-dir':(str(project)+'\n',0o644),
            self.system_root/'usr/local/sbin/vpnctl':((self.project/'vpnctl').read_bytes(),0o755),
            self.system_root/'usr/local/sbin/awg-easy-3':((self.project/'awg-easy-3').read_bytes(),0o755),
            **vpnctl_boot.files(self.system_root,vpnctl_boot.backend(self.system_root)),
        }


    def sync_boot_hook(self):
        self.install_stage('Настройка проверки обновлений после загрузки ОС')
        try:
            result=vpnctl_boot.configure(self,self.config().get('auto_check',False))
            if result['backend']=='unsupported':terminal_message('Предупреждение: systemd/OpenRC недоступен; автоматическая проверка после загрузки не включена',file=sys.stderr)
            return result
        except (ManagerError,OSError):
            terminal_message('Предупреждение: VPN установлен, но boot updater не настроен; выполните vpnctl auto-update enable',file=sys.stderr)
            return {'enabled':False,'configuration_required':True}


    def register(self,config):
        self.install_stage('Регистрация vpnctl и системных файлов')
        self.installed_project=self.install_sources()
        records={}
        if self.journal.exists():
            journal=json.loads(self.journal.read_text())
            if 'manager_files_before' not in journal:
                journal['manager_files_before']={}
                for filename,(content,_mode) in self.system_files().items():
                    journal['manager_files_before'][str(filename)]={
                        'before':base64.b64encode(filename.read_bytes()).decode() if filename.exists() else None,
                        'mode':filename.stat().st_mode&0o777 if filename.exists() else None,
                        'after_sha256':hashlib.sha256(content.encode() if isinstance(content,str) else content).hexdigest()}
                self.save_journal(journal)
        for filename,(content,mode) in self.system_files().items():
            atomic_write(filename,content,mode)
            records[str(filename)]=hashlib.sha256(content.encode() if isinstance(content,str) else content).hexdigest()
        config['manager_files']=records
        self.write_config(config)


    def install_sources(self):
        import vpnctl_sources
        return vpnctl_sources.install(self)


    def restore_manager_files(self,journal):
        allowed={str(filename) for filename in self.system_files()}
        for filename,record in journal.get('manager_files_before',{}).items():
            if filename not in allowed:raise ManagerError('Неизвестный системный путь в журнале восстановления',65)
            path=Path(filename)
            before=base64.b64decode(record['before'],validate=True) if record['before'] is not None else None
            if path.exists():
                if path.is_symlink() or not path.is_file():raise ManagerError('Системный файл изменён другим приложением: '+filename,73)
                actual=path.read_bytes()
                if actual==before:continue
                if hashlib.sha256(actual).hexdigest()!=record['after_sha256']:
                    raise ManagerError('Системный файл изменён другим приложением: '+filename,73)
            if before is None:path.unlink(missing_ok=True)
            else:atomic_write(path,before,record['mode'])


    def check_system_paths(self,existing=None,legacy=None):
        records=(existing or {}).get('manager_files',{})
        for filename in self.system_files():
            if not filename.exists() and not filename.is_symlink():continue
            digest=hashlib.sha256(filename.read_bytes()).hexdigest() if filename.is_file() and not filename.is_symlink() else None
            if digest and records.get(str(filename))==digest:continue
            original=Path(legacy['directory'])/'awg-easy-3' if legacy else None
            if legacy and filename==self.system_root/'usr/local/sbin/awg-easy-3' and original.is_file() and not filename.is_symlink() and filename.read_bytes()==original.read_bytes():continue
            raise ManagerError('Системный путь уже занят; чужой файл не изменён: '+str(filename),73)

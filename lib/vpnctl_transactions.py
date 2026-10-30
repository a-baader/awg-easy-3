"""Журнал транзакций и их подтверждённое завершение; сетевые действия делегируются manager."""
import base64
import copy
import os
from pathlib import Path
from vpnctl_common import ManagerError, atomic_json, atomic_write
from vpnctl_types import Journal
import vpnctl_retired

class TransactionManager:
    def save_journal(self,journal:Journal):
        journal.setdefault('owner_pid',os.getpid())
        parent=str(os.getppid())
        journal.setdefault('lock_owner_pid',os.getppid() if os.environ.get('VPNCTL_BOOTSTRAP_LOCK')==parent else os.getpid())
        atomic_json(self.journal,journal)


    def freeze(self,journal):
        # Сначала сохраняется намерение: recover снимет запрет и после аварии RPC.
        if 'running_before' not in journal:
            journal['running_before']=[]
            for name in journal['selected']:
                info=self.inspect_container(journal['previous']['components'][name]['container'])
                if info and info['State'].get('Running'):journal['running_before'].append(name)
        self.save_journal(journal)
        config=copy.deepcopy(journal['previous'])
        config['maintenance_components']=journal['selected']
        for name in journal['selected']:
            item=config['components'][name]
            info=self.inspect_container(item['container'])
            if info and info['State'].get('Running'): self.rpc(item,'maintenance.begin')
        self.write_config(config)
        return config


    def commit_transaction(self,journal):
        # После этой точки откат запрещён: панель могла уже принять новые изменения.
        candidate=copy.deepcopy(journal['candidate'])
        candidate['maintenance_components']=journal['selected']
        self.write_config(candidate)
        committed={**journal,'phase':'committed'}
        journal['phase']='commit_uncertain'
        self.save_journal(committed)
        journal.update(committed)
        self.finish_committed(journal)


    def finish_committed(self,journal):
        self.verify_transaction_containers(journal)
        if journal['operation']=='uninstall':
            return self.finish_uninstall(journal)
        candidate=copy.deepcopy(journal['candidate']);candidate.pop('maintenance_components',None)
        self.write_config(candidate)
        for name in journal['selected']:
            if name not in candidate['components']: continue
            item=candidate['components'][name]
            # Recover запускает и контейнер, остановленный в момент аварии.
            self.compose('up','-d','--no-deps',name+'-easy');self.health(item)
            info=self.inspect_container(item['container'])
            if info and info['State'].get('Running'):
                self.rpc(item,'maintenance.end')
        vpnctl_retired.cleanup(self,journal)
        self.journal.unlink(missing_ok=True)


    def commit_rollback(self,journal):
        completed={**journal,'phase':'rollback_committed'}
        journal['phase']='commit_uncertain'
        self.save_journal(completed)
        journal.update(completed)
        self.finish_rollback(journal)


    def finish_rollback(self,journal):
        # Данные уже восстановлены. Повторный recover не заменяет их snapshot.
        self.verify_transaction_containers(journal)
        previous=journal['previous']
        self.restore_manager_files(journal)
        if journal['operation']=='reinstall':
            for name in journal['selected']:
                for suffix in ['.conf','.vpn-url']:
                    filename=name+suffix;path=self.root/'bootstrap'/filename
                    value=journal.get('bootstrap_before',{}).get(filename)
                    if value is None:path.unlink(missing_ok=True)
                    else:atomic_write(path,base64.b64decode(value,validate=True))
        if journal['operation']=='install' and previous.get('legacy_origin'):
            item=previous['components']['awg'];info=self.inspect_container(item['container'])
            if info and info.get('Image')!=item['image']:
                self.run(['docker','rm','-f',item['container']])
            self.run(['env','AWG_IMAGE='+item['image'],'docker','compose','--project-directory',previous['project'],'-f',str(Path(previous['project'])/'docker-compose.yml'),'up','-d','--pull','never','awg-easy'])
            self.health(item);self.path.unlink(missing_ok=True)
        else:
            self.write_config(previous)
            for name in journal['selected']:
                if name in previous['components']:
                    self.compose('up','-d','--no-deps',name+'-easy');self.health(previous['components'][name])
                    info=self.inspect_container(previous['components'][name]['container'])
                    if info and info['State'].get('Running'):self.rpc(previous['components'][name],'maintenance.end')
        vpnctl_retired.cleanup(self,journal)
        self.journal.unlink(missing_ok=True)


    def verify_transaction_containers(self,journal):
        # Журнал не даёт права останавливать чужой контейнер с совпавшим именем.
        for name in journal['selected']:
            allowed=[config['components'][name] for config in [journal.get('previous'),journal.get('candidate')]
                if config and name in config['components']]
            for container in {item['container'] for item in allowed}:
                info=self.inspect_container(container)
                if not info:continue
                source=next((m['Source'] for m in info.get('Mounts',[]) if m['Destination']=='/data'),None)
                labels=info.get('Config',{}).get('Labels',{})
                matches=any(info.get('Image')==item['image'] and source==item['data_directory'] for item in allowed if item['container']==container)
                if not matches or info.get('HostConfig',{}).get('NetworkMode')!='host' or labels.get('com.docker.compose.service')!=name+'-easy':
                    raise ManagerError('Восстановление остановлено: владение контейнером не подтверждено: '+container,73)

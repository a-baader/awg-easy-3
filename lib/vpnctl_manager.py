"""Единый host manager wg-dual-easy. Секреты не передаются через argv."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import ipaddress
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid

from dual_preflight import PreflightError, environment_check, inspect_conflicts, validate_instances
from vpnctl_common import ManagerError, atomic_json, atomic_write, execute, global_lock, redact, durable_replace
from vpnctl_backup import create_archive, verify_archive, extract_component
import vpnctl_boot
from vpnctl_progress import installation_progress
from vpnctl_output import emit_result,format_human

UPDATE_API_VERSION=1

def ask(message):
    print(message,end='',file=sys.stderr,flush=True)
    return input()

class Parser(argparse.ArgumentParser):
    def __init__(self,*args,**kwargs):
        kwargs['add_help']=False
        super().__init__(*args,**kwargs)
        self.add_argument('-h','--help','-help',action='help',help='Показать справку')
        self._positionals.title='Команды';self._optionals.title='Параметры'
    def format_usage(self): return super().format_usage().replace('usage:','Использование:',1)
    def format_help(self): return super().format_help().replace('usage:','Использование:',1)
    def error(self, message):
        print('Ошибка параметров. Используйте vpnctl help.\n' + self.format_usage(), file=sys.stderr)
        raise SystemExit(64)

class Manager:
    def __init__(self, project, config='/etc/vpn-easy-dual/config.json', runner=execute, system_root='/'):
        self.project=Path(project).resolve(); self.path=Path(config).absolute(); self.run=runner
        self.root=self.path.parent; self.journal=self.root/'transaction.json'
        self.compose_path=self.root/'compose.json'
        self.bootstrap_results={}
        self.resolved_releases={}
        self.system_root=Path(system_root)

    def install_stage(self,message):
        progress=getattr(self,'_installation_progress',None)
        if progress:progress.stage(message)

    def config(self, required=True):
        if not self.path.is_file():
            if required: raise ManagerError('wg-dual-easy ещё не установлен',65)
            return None
        try: value=json.loads(self.path.read_text())
        except (OSError,ValueError): raise ManagerError('Конфигурация vpnctl повреждена',65) from None
        if value.get('config_version')!=1: raise ManagerError('Неизвестная версия конфигурации vpnctl',65)
        validate_instances(value['components'])
        return value

    def selected(self, config, component='all'):
        if component=='all': return list(config['components'])
        if component not in config['components']: raise ManagerError('Компонент не установлен: '+component,65)
        return [component]

    def inspect_container(self, name):
        raw=self.run(['docker','inspect',name],check=False)
        return json.loads(raw)[0] if raw else None

    def rpc(self, item, operation, args=None):
        info=self.inspect_container(item['container'])
        name='wg' if item['backend']=='wireguard' else 'awg'
        source=next((mount['Source'] for mount in (info or {}).get('Mounts',[]) if mount['Destination']=='/data'),None)
        if not info or info.get('Image')!=item['image'] or source!=item['data_directory'] or info.get('HostConfig',{}).get('NetworkMode')!='host' or info.get('Config',{}).get('Labels',{}).get('com.docker.compose.service')!=name+'-easy':
            raise ManagerError('Локальная операция остановлена: владение контейнером не подтверждено',73)
        raw=self.run(['docker','exec','-i',item['container'],'node','/app/control-client.js'], input=json.dumps({'operation':operation,'args':args or {}},ensure_ascii=False)+'\n')
        try: result=json.loads(raw)
        except ValueError: raise ManagerError('Некорректный ответ локального канала управления',74) from None
        if not result.get('ok'):
            messages={'LAST_HOME':'Нельзя отключить последнего Home-клиента','CURRENT_PANEL_PATH':'Нельзя отозвать текущий путь доступа к панели',
                'UNSUPPORTED_FORMAT':'Этот backend не поддерживает выбранный формат экспорта','CLIENT_NOT_FOUND':'Клиент не найден',
                'CLIENT_NAME_EXISTS':'Клиент с таким именем уже существует','GEO_UNAVAILABLE':'База GeoIP недоступна'}
            raise ManagerError(messages.get(result.get('code'),'Не удалось выполнить операцию приложения'))
        return result['value']

    def write_config(self, config):
        validate_instances(config['components'])
        atomic_json(self.path,config); self.write_compose(config)

    def write_compose(self, config):
        services={}
        for name,item in config['components'].items():
            services[name+'-easy']={'image':item['image'],'pull_policy':'never','container_name':item['container'],
              'network_mode':'host','restart':'unless-stopped','cap_add':['NET_ADMIN'],
              'devices':['/dev/net/tun:/dev/net/tun'],'volumes':[item['data_directory']+':/data'],
              'read_only':True,'tmpfs':['/run:mode=0700,size=16m','/tmp:mode=1777,size=32m'],
              'security_opt':['no-new-privileges:true'],'stop_grace_period':'15s',
              'environment':{'VPN_BACKEND':item['backend'],'VPN_DUAL_MODE':'1','VPN_LANG':config.get('language','en'),
                  'VPN_MAINTENANCE':'1' if name in config.get('maintenance_components',[]) else '0'}}
        atomic_json(self.compose_path,{'name':'vpn-easy-dual','services':services})

    def compose(self,*args):
        return self.run(['docker','compose','--project-directory',str(self.project),'-f',str(self.compose_path),*args])

    def health(self,item):
        for _ in range(60):
            info=self.inspect_container(item['container'])
            if not info or not info['State'].get('Running'): raise ManagerError('Контейнер не работает: '+item['container'])
            health=info['State'].get('Health',{}).get('Status')
            if health=='healthy': return
            if health=='unhealthy': raise ManagerError('Healthcheck не пройден: '+item['container'])
            time.sleep(1)
        raise ManagerError('Истёк срок ожидания healthcheck: '+item['container'])

    def owned(self,config):
        proof={}
        for name,item in config['components'].items():
            info=self.inspect_container(item['container'])
            if not info: continue
            source=next((m['Source'] for m in info.get('Mounts',[]) if m['Destination']=='/data'),None)
            if source!=item['data_directory'] or info.get('Image')!=item['image'] or info.get('HostConfig',{}).get('NetworkMode')!='host' or info['Config'].get('Labels',{}).get('com.docker.compose.service')!=name+'-easy':
                raise ManagerError('Не подтверждено владение контейнером: '+item['container'],73)
            proof[name]={**item,'data_directory':source}
        return proof

    def resolve_image_reference(self,reference):
        if not reference.startswith('release://'):return reference
        try:version,name=reference[len('release://'):].rsplit('/',1)
        except ValueError:raise ManagerError('Некорректная ссылка на релиз',65) from None
        if name not in ('awg','wg'):raise ManagerError('Неизвестный компонент релиза',65)
        vpnctl_boot.stable_version(version)
        if version not in self.resolved_releases:
            self.install_stage('Получение и проверка манифеста релиза')
            try:value=vpnctl_boot.fetch_manifest(version)
            except Exception as error:raise ManagerError('Не удалось проверить manifest выбранного релиза; укажите опубликованные digest или используйте --build',69) from error
            atomic_json(self.root/'releases'/('v'+version+'.json'),value)
            self.resolved_releases[version]=value
        return self.resolved_releases[version]['images'][name]

    def image(self,reference,backend):
        if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]*',reference): raise ManagerError('Некорректная ссылка на образ',65)
        raw=self.run(['docker','image','inspect',reference],check=False)
        if raw is None:
            if '@sha256:' not in reference: raise ManagerError('Образ отсутствует локально; укажите digest или сначала соберите target',69)
            self.install_stage('Загрузка образа '+('AWG' if backend=='amneziawg' else 'WG'))
            self.run(['docker','pull',reference]); raw=self.run(['docker','image','inspect',reference])
        info=json.loads(raw)[0]
        if info.get('Os')!='linux' or info.get('Architecture')!='amd64': raise ManagerError('Образ должен быть Linux/amd64',65)
        self.install_stage('Проверка образа '+('AWG' if backend=='amneziawg' else 'WG'))
        env=info['Config'].get('Env',[])
        if 'VPN_BACKEND='+backend not in env: raise ManagerError('Backend образа не соответствует компоненту',65)
        self.run(['docker','run','--rm','--network','none','--read-only','--entrypoint','node',info['Id'],'-e',
            "require('/app/lib/VpnBackend').createBackend(process.env.VPN_BACKEND).validateEnvironment().then(()=>{require('node:fs').accessSync('/app/control-client.js')}).catch(()=>process.exit(1))"])
        return info['Id']

    def build_images(self,args,selected):
        environment=os.environ.copy()
        if self.run(['docker','buildx','version'],check=False) is None:
            environment['DOCKER_BUILDKIT']='0'
        for name in selected:
            reference=args.awg_image if name=='awg' else args.wg_image
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:-]*',reference) or reference.startswith('sha256:'):
                raise ManagerError('Для --build укажите локальный tag образа',64)
            self.install_stage('Сборка образа '+name.upper())
            self.run(['docker','build','--target',name,'--tag',reference,str(self.project)],env=environment)

    def validate_release_image(self,item,version):
        actual=self.run(['docker','run','--rm','--network','none','--read-only','--entrypoint','node',item['image'],
            '-p',"require('/app/package.json').version"]).strip()
        if actual!=version:raise ManagerError('Версия приложения в образе не совпадает с манифестом релиза',65)

    def status(self,component='all'):
        config=self.config(False)
        if not config: return {'installed':False,'components':{}}
        result={}
        for name in self.selected(config,component):
            item=config['components'][name]; info=self.inspect_container(item['container'])
            result[name]={'container':item['container'],'running':bool(info and info['State'].get('Running')),
                'health':info['State'].get('Health',{}).get('Status') if info else None,
                'image':info.get('Image') if info else item['image'],'interface':item['interface'],
                'panel_url':f"http://{item['address4']}:{item['panel_port']}/"}
        return {'installed':True,'version':config.get('version'),'components':result,'recovery_required':self.journal.exists()}

    def lifecycle(self,operation,component):
        config=self.config(); selected=self.selected(config,component); self.owned(config)
        for name in selected:
            service=name+'-easy'
            if operation=='start': self.compose('up','-d','--no-deps',service); self.health(config['components'][name])
            elif operation=='stop': self.compose('stop',service)
            elif operation=='restart': self.compose('restart',service); self.health(config['components'][name])
        return self.status(component)

    @contextmanager
    def stopped(self,config,selected):
        running=[]
        try:
            for name in selected:
                info=self.inspect_container(config['components'][name]['container'])
                if info and info['State'].get('Running'):
                    running.append(name); self.compose('stop',name+'-easy')
            yield running
        finally:
            for name in running:
                self.compose('up','-d','--no-deps',name+'-easy'); self.health(config['components'][name])

    def backup(self,component,output=None):
        config=self.config(); selected=self.selected(config,component); self.owned(config)
        return self.backup_selected(config,selected,output)

    def backup_selected(self,config,selected,output=None):
        self.install_stage('Создание резервной копии')
        destination=output or str(self.root/'backups'/('backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.tar.gz'))
        for item in config['components'].values():
            data=Path(item['data_directory']).resolve(); target=Path(destination).resolve()
            if target==data or data in target.parents: raise ManagerError('Резервная копия должна храниться вне каталогов данных',64)
        with self.stopped(config,selected): return create_archive(config,selected,destination)

    def save_journal(self,journal):
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
        self.journal.unlink(missing_ok=True)

    def packet_probe(self,item,options):
        command=['docker','run','--rm','-i','--log-driver','none','--network','bridge','--cap-drop','ALL',
            '--read-only','--tmpfs','/run:mode=0700,size=16m','--tmpfs','/tmp:mode=1777,size=16m',
            '--security-opt','no-new-privileges:true']
        if 'nativeConfig' in options: command+=['--cap-add','NET_ADMIN','--device','/dev/net/tun']
        command+=['--entrypoint','node',item['image'],'/app/probe-client.js']
        try: return json.loads(self.run(command,input=json.dumps(options)+'\n'))
        except (ManagerError,ValueError):return {'status':'failed','packet_proof':False}

    def diagnose(self,component,args=None):
        config=self.config(); result={}
        profile=None
        if args and args.probe_profile:
            if component=='all':raise ManagerError('Для тестового профиля укажите компонент awg или wg',64)
            path=Path(args.probe_profile)
            if path.is_symlink() or not path.is_file() or path.stat().st_size>16384 or path.stat().st_mode&0o077:
                raise ManagerError('Тестовый профиль должен быть обычным файлом до 16 KiB с правами 0600',65)
            profile=path.read_text()
        for name in self.selected(config,component):
            item=config['components'][name]
            try: app=self.rpc(item,'diagnose')
            except ManagerError: app={'engine':False,'panel':False,'packet_probes':{'status':'not_run'}}
            table=self.run(['nft','list','table','inet',item['table']],check=False)
            socket=self.run(['ss','-Hlun','sport = :'+str(item['udp_port'])],check=False)
            route=self.run(['ip','-j','-4','route','show','dev',item['interface']],check=False)
            forwarding={}
            for family,filename in [('ipv4','/proc/sys/net/ipv4/ip_forward'),('ipv6','/proc/sys/net/ipv6/conf/all/forwarding')]:
                try: forwarding[family]=Path(filename).read_text().strip()=='1'
                except OSError: forwarding[family]=False
            result[name]={'application':app,'udp_socket':bool(socket and socket.strip()),'route':bool(route and json.loads(route)),
                'forwarding':forwarding,'firewall':bool(table),'isolation_rules':bool(table and 'deny VPN access to other host services' in table),
                'negative_peer_probes':'requires_test_peers; см. scripts/test-dual-network.cjs'}
            if profile:
                own=f"http://{item['address4']}:{item['panel_port']}/api/v1/session"
                requests=[{'name':'own_panel','url':own,'allow':args.peer_role=='home'},
                    {'name':'internet_http','url':args.internet_url,'allow':True}]
                for foreign,other in config['components'].items():
                    if foreign!=name:requests.append({'name':'foreign_panel','url':f"http://{other['address4']}:{other['panel_port']}/api/v1/session",'allow':False})
                endpoint=item['endpoint'];host='['+endpoint+']' if ':' in endpoint else endpoint
                for other in config['components'].values():requests.append({'name':'public_host_panel_'+str(other['panel_port']),
                    'url':f"http://{host}:{other['panel_port']}/api/v1/session",'allow':False})
                result[name]['peer_probe']=self.packet_probe(item,{'nativeConfig':profile,'requests':requests,'dns':args.dns})
                result[name]['negative_peer_probes']='performed_for_selected_profile; другие роли проверяются отдельно'
        return result

    def log_result(self,operation,result):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=self.root/'operations.jsonl'
        entry={'time':datetime.now(timezone.utc).isoformat(),'component':'manager','operation':operation,'result':result}
        # В журнал попадает только статус операции, не ответы export/reset-password.
        fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w') as stream: stream.write(json.dumps(entry,ensure_ascii=False)+'\n')

    def legacy(self):
        info=self.inspect_container('awg-easy-3')
        if not info: return None
        labels=info['Config'].get('Labels',{})
        source=next((m['Source'] for m in info.get('Mounts',[]) if m['Destination']=='/data'),None)
        directory=labels.get('com.docker.compose.project.working_dir')
        if labels.get('com.docker.compose.service')!='awg-easy' or not source or not directory or info.get('HostConfig',{}).get('NetworkMode')!='host':
            raise ManagerError('Существующая установка AWG не распознана; она не изменена',65)
        if 'VPN_DUAL_MODE=1' in info['Config'].get('Env',[]):
            raise ManagerError('Найден Dual-контейнер без конфигурации manager; восстановите конфигурацию из backup',65)
        try:self.validate_release_image({'image':info['Image']},'0.1.6')
        except ManagerError:raise ManagerError('Миграция поддерживает исходный AWG 0.1.6; другая версия не изменена',65) from None
        try: state=json.loads((Path(source)/'state.json').read_text())
        except (OSError,ValueError): raise ManagerError('Состояние исходного AWG не распознано',65) from None
        if state.get('version') not in (1,2) or state.get('server',{}).get('backend','amneziawg')!='amneziawg':
            raise ManagerError('Поддерживается миграция оригинального AWG 0.1.6 со схемой v1/v2; неизвестная установка не изменена',65)
        server=state['server']
        item={'backend':'amneziawg','table':'awg_easy_3','interface':server['interfaceName'],'container':'awg-easy-3',
              'network4':server['ipv4Subnet'],'address4':server['address4'],'network6':server.get('ipv6Subnet'),
              'address6':server.get('address6'),'udp_port':server['listenPort'],'panel_port':server['panelPort'],
              'data_directory':str(Path(source).resolve()),'image':info['Image'],'image_ref':info['Image'],
              'endpoint':server['endpointHost'],'wan_interface':server['wanInterface'],'ipv6_mode':server.get('ipv6Mode')}
        if info['State'].get('Running'):
            public=self.run(['docker','exec',item['container'],'awg','show',item['interface'],'public-key']).strip()
            port=self.run(['docker','exec',item['container'],'awg','show',item['interface'],'listen-port']).strip()
            if public!=server['publicKey'] or port!=str(item['udp_port']):
                raise ManagerError('Работающий AWG не соответствует найденному состоянию; миграция остановлена',73)
        return {'item':item,'directory':directory,'running':info['State'].get('Running',False)}

    def plan_install(self,args):
        existing=getattr(self,'planning_existing',self.config(False))
        if existing and set(existing['components'])=={'awg','wg'}: return existing
        legacy=None if existing or getattr(self,'reinstall_previous',None) else self.legacy()
        self.reserved_ports=set()
        self.interactive=sys.stdin.isatty() and not args.yes
        if legacy:
            self.reserved_ports.update({('udp',legacy['item']['udp_port']),('tcp',legacy['item']['panel_port'])})
        for item in (existing or {}).get('components',{}).values():
            self.reserved_ports.update({('udp',item['udp_port']),('tcp',item['panel_port'])})
        endpoint=args.host or next((item.get('endpoint') for item in (existing or {}).get('components',{}).values() if item.get('endpoint')),None)
        routes=json.loads(self.run(['ip','-j','-4','route','get','1.1.1.1']))
        if not routes or not routes[0].get('dev'):raise ManagerError('Не найден IPv4 маршрут к Internet',65)
        wan=routes[0]['dev']
        if not endpoint:
            endpoint=routes[0].get('prefsrc') or routes[0].get('src')
            if not endpoint:raise ManagerError('Не удалось определить endpoint; укажите --host',64)
        try:
            ipaddress.ip_address(endpoint)
            if '%' in endpoint: raise ValueError()
        except ValueError:
            if not isinstance(endpoint,str) or len(endpoint)>253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in endpoint.split('.')):
                raise ManagerError('Некорректный публичный IP или домен сервера',64) from None
        data_root=Path(args.data_root or self.project).resolve()
        components={}
        for name,n,backend,table,container,panel in [('awg',8,'amneziawg','awg_easy_3','awg-easy-3',51821 if args.awg_panel_port is None else args.awg_panel_port),('wg',9,'wireguard','wg_easy_3','wg-easy-dual',51825 if args.wg_panel_port is None else args.wg_panel_port)]:
            if hasattr(self,'planning_targets') and name not in self.planning_targets and name not in (existing or {}).get('components',{}):continue
            if existing and name in existing['components']:
                components[name]=copy.deepcopy(existing['components'][name]);components[name].setdefault('version',existing['version']);continue
            if name=='awg' and legacy:
                components[name]={**legacy['item'],'image_ref':args.awg_image}
                continue
            port=args.awg_port if name=='awg' else 51824 if args.wg_port is None else args.wg_port
            if name=='awg' and port is None:
                for _ in range(128):
                    candidate=secrets.randbelow(40001)+20000
                    if candidate!=51820 and candidate!=(51824 if args.wg_port is None else args.wg_port) and not self.port_used('udp',candidate): port=candidate; break
                if port is None: raise ManagerError('Не найден свободный случайный AWG UDP-порт',73)
            port=self.choose_port('udp',port,(args.awg_port if name=='awg' else args.wg_port) is not None)
            panel=self.choose_port('tcp',panel,(args.awg_panel_port if name=='awg' else args.wg_panel_port) is not None)
            self.reserved_ports.update({('udp',port),('tcp',panel)})
            subnet=args.awg_subnet if name=='awg' else args.wg_subnet
            try: address=str(ipaddress.IPv4Network(subnet).network_address+1)
            except ValueError: raise ManagerError('Некорректная VPN-подсеть',64) from None
            components[name]={'backend':backend,'table':table,'interface':args.awg_interface if name=='awg' else args.wg_interface,'container':container,
                'network4':subnet,'address4':address,'udp_port':port,'panel_port':panel,
                'data_directory':str(data_root/('data' if name=='awg' else 'data-wg')),'endpoint':endpoint,'wan_interface':wan,
                'image_ref':args.awg_image if name=='awg' else args.wg_image}
        routes6=json.loads(self.run(['ip','-j','-6','route','show','default']))
        usable6=False
        if any(route.get('dev')==wan for route in routes6):
            links6=json.loads(self.run(['ip','-j','-6','address','show','dev',wan]))
            usable6=any(address.get('family')=='inet6' and address.get('scope')=='global'
                and not any(flag in address.get('flags',[]) for flag in ['tentative','dadfailed','deprecated'])
                for link in links6 for address in link.get('addr_info',[]))
        if usable6:
            for name,item in components.items():
                if (name=='awg' and legacy) or (existing and name in existing['components']): continue
                token=secrets.token_hex(5); prefix='fd'+token[:2]+':'+token[2:6]+':'+token[6:]
                item.update({'network6':prefix+'::/64','address6':prefix+'::1','ipv6_mode':'nat66'})
        config={**(existing or {}),'config_version':1,'version':(self.project/'VERSION').read_text().strip(),'project':str(self.project),
            'language':args.lang or (existing or {}).get('language','en'),'auto_check':(existing or {}).get('auto_check',True),'components':components}
        for item in components.values():item.setdefault('version',config['version'])
        if legacy: config['legacy_origin']=legacy
        validate_instances(components)
        for item in components.values():
            data=Path(item['data_directory']).resolve()
            if data==self.root.resolve() or data in self.root.resolve().parents:
                raise ManagerError('Каталог manager не должен находиться внутри данных VPN',73)
        return config

    def port_used(self,protocol,port):
        if (protocol,port) in getattr(self,'reserved_ports',set()): return True
        key='udp_port' if protocol=='udp' else 'panel_port'
        own_port=any(item[key]==port for item in getattr(self,'reinstall_previous',{}).get('components',{}).values())
        if self.run(['ss','-Huan' if protocol=='udp' else '-Hltn','sport = :'+str(port)]).strip() and not own_port: return True
        identifiers=self.run(['docker','ps','-q']).split()
        for identifier in identifiers:
            raw=self.run(['docker','inspect','--format','{{json .NetworkSettings.Ports}}',identifier],check=False)
            if raw is None:
                if not self.run(['docker','ps','-aq','--filter','id='+identifier]).strip():continue
                raise ManagerError('Не удалось проверить порты существующего контейнера',73)
            ports=json.loads(raw or 'null') or {}
            if any(key.endswith('/'+protocol) and any(int(b['HostPort'])==port for b in bindings or []) for key,bindings in ports.items()): return True
        return False

    def choose_port(self,protocol,port,explicit):
        if type(port) is not int or not 1<=port<=65535: raise ManagerError('Порт должен быть от 1 до 65535',64)
        if not self.port_used(protocol,port): return port
        candidates=range(port+1,65536)
        suggestion=next((candidate for candidate in candidates if not self.port_used(protocol,candidate)),None)
        if suggestion is None:suggestion=next((candidate for candidate in range(1024,port) if not self.port_used(protocol,candidate)),None)
        if not suggestion: raise ManagerError('Не найден свободный порт',73)
        if explicit: raise ManagerError('Явно заданный порт занят: '+str(port)+'/'+protocol+'; укажите свободный порт, например '+str(suggestion),73)
        if not getattr(self,'interactive',sys.stdin.isatty()): raise ManagerError('Порт по умолчанию занят; явно укажите свободный порт, например '+str(suggestion),73)
        while True:
            try:answer=ask('Порт '+str(port)+'/'+protocol+' занят. Другой порт ['+str(suggestion)+']: ').strip()
            except EOFError:raise ManagerError('Выбор порта отменён',64) from None
            value=int(answer) if answer.isdecimal() else suggestion if not answer else 0
            if 1<=value<=65535 and not self.port_used(protocol,value):return value
            print('Ошибка: укажите свободный порт от 1 до 65535',file=sys.stderr)

    def validate_state_image(self,item,directory=None):
        source=directory or item['data_directory']
        self.run(['docker','run','--rm','--network','none','--read-only','-v',str(source)+':/data:ro','--entrypoint','node',item['image'],'-e',
          "const {StateStore}=require('/app/lib/StateStore');new StateStore('/data/state.json').load().then(s=>{if(!s)throw Error();require('/app/lib/VpnBackend').createBackend(process.env.VPN_BACKEND).assertState(s)}).catch(()=>process.exit(1))"])

    def initialize(self,name,item,config):
        self.install_stage('Подготовка конфигурации и профиля '+name.upper())
        directory=Path(item['data_directory'])
        if (directory/'state.json').exists(): self.validate_state_image(item); return
        if directory.exists() and any(directory.iterdir()): raise ManagerError('Каталог данных не пуст и не распознан: '+str(directory),73)
        directory.mkdir(parents=True,exist_ok=True,mode=0o700); directory.chmod(0o700)
        network=ipaddress.ip_network(item['network4'])
        options={'endpointHost':item['endpoint'],'wanInterface':item['wan_interface'],'listenPort':item['udp_port'],'firstClientName':'admin',
          'panelPort':item['panel_port'],'uiLanguage':config['language'],'interfaceName':item['interface'],
          'ipv4Subnet':item['network4'],'serverAddress4':item['address4'],'firstClientAddress4':str(network.network_address+2),'ipv6':None}
        if item.get('network6'):
            network6=ipaddress.ip_network(item['network6'])
            options['ipv6']={'subnet':item['network6'],'serverAddress':item['address6'],'firstClientAddress':str(network6.network_address+2),'mode':item.get('ipv6_mode','nat66')}
        raw=self.run(['docker','run','--rm','-i','--log-driver','none','--network','host','--read-only','--tmpfs','/tmp:mode=1777',
            '-v',str(directory)+':/data','--entrypoint','node',item['image'],'/app/server.js','init-json'],input=json.dumps(options)+'\n')
        result=json.loads(raw)
        bootstrap=self.root/'bootstrap'; bootstrap.mkdir(parents=True,exist_ok=True,mode=0o700)
        atomic_write(bootstrap/(name+'.conf'),result['nativeConfig'])
        if result.get('vpnLink'): atomic_write(bootstrap/(name+'.vpn-url'),result['vpnLink']+'\n')
        # Пароль остаётся только в памяти до однократного вывода оператору.
        self.bootstrap_results[name]={'password':result['bootstrapPassword'],'panel_url':result['panelUrl'],
            'profile_file':str(bootstrap/(name+'.conf')),'vpn_url':result.get('vpnLink')}

    def system_files(self):
        return {
            self.system_root/'etc/sysctl.d/99-vpn-easy-dual.conf':('net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n',0o644),
            self.system_root/'etc/modules-load.d/vpn-easy-dual.conf':('tun\n',0o644),
            self.system_root/'etc/vpn-easy-dual-install-dir':(str(self.project)+'\n',0o644),
            self.system_root/'usr/local/sbin/vpnctl':((self.project/'vpnctl').read_bytes(),0o755),
            self.system_root/'usr/local/sbin/awg-easy-3':((self.project/'awg-easy-3').read_bytes(),0o755),
            **vpnctl_boot.files(self.system_root,vpnctl_boot.backend(self.system_root)),
        }

    def sync_boot_hook(self):
        self.install_stage('Настройка проверки обновлений после загрузки ОС')
        try:
            result=vpnctl_boot.configure(self,self.config().get('auto_check',False))
            if result['backend']=='unsupported':print('Предупреждение: systemd/OpenRC недоступен; автоматическая проверка после загрузки не включена',file=sys.stderr)
            return result
        except (ManagerError,OSError):
            print('Предупреждение: VPN установлен, но boot updater не настроен; выполните vpnctl auto-update enable',file=sys.stderr)
            return {'enabled':False,'configuration_required':True}

    def register(self,config):
        self.install_stage('Регистрация vpnctl и системных файлов')
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

    @installation_progress
    def install(self,args):
        self.install_stage('Проверка системы и текущей установки')
        existing=self.config(False)
        if existing and set(existing['components'])=={'awg','wg'}: return {'installed':True,'unchanged':True,'components':list(existing['components'])}
        if existing: return self.extend(existing,args)
        environment_check(run=lambda command:self.run(command))
        self.install_stage('Проверка сети, портов и системных ресурсов')
        plan=self.plan_install(args)
        legacy=plan.get('legacy_origin')
        self.check_system_paths(legacy=legacy)
        proof={'awg':legacy['item']} if legacy else {}
        inspect_conflicts(plan['components'],owned=proof,run=lambda command:self.run(command))
        self.install_stage('Подготовка образов AWG и WG')
        if args.build:self.build_images(args,list(plan['components']))
        for name,item in plan['components'].items():
            item['image_ref']=self.resolve_image_reference(item['image_ref'])
            item['image']=self.image(item['image_ref'],item['backend'])
            self.validate_release_image(item,plan['version'])
            directory=Path(item['data_directory'])
            if (directory/'state.json').exists():
                if name!='awg' or not legacy: raise ManagerError('Существующее состояние не принадлежит известной установке',73)
                self.validate_state_image(item)
            elif directory.exists() and any(directory.iterdir()): raise ManagerError('Каталог данных уже занят',73)
        previous=None
        if legacy:
            previous={'config_version':1,'version':'0.1.6','language':args.lang,'project':legacy['directory'],'components':{'awg':legacy['item']},'legacy_origin':legacy}
        journal={'operation':'install','phase':'prepared','candidate':plan,'previous':previous,'selected':list(plan['components'])}
        self.save_journal(journal)
        try:
            if legacy:
                self.install_stage('Резервная копия и миграция существующего AWG')
                self.run(['sh','-s',legacy['directory']],input='project_dir=$1\n. "$project_dir/lib/auto-update.sh"\nawg_easy_auto_update_disable\n')
                self.run(['docker','stop',legacy['item']['container']])
                snapshot=str(self.root/'backups'/('before-migration-'+uuid.uuid4().hex+'.tar.gz'))
                create_archive(previous,['awg'],snapshot)
                journal.update({'snapshot':snapshot,'phase':'snapshot_verified'});self.save_journal(journal)
                self.run(['docker','rm',legacy['item']['container']])
            for name,item in plan['components'].items(): self.initialize(name,item,plan)
            plan['maintenance_components']=list(plan['components'])
            self.write_config(plan)
            self.run(['sysctl','-w','net.ipv4.ip_forward=1']);self.run(['sysctl','-w','net.ipv6.conf.all.forwarding=1'])
            for name,item in plan['components'].items():
                self.install_stage('Запуск '+name.upper()+' и ожидание готовности')
                self.compose('up','-d','--no-deps',name+'-easy');self.health(item)
            self.register(plan)
            journal['candidate']=copy.deepcopy(plan)
            self.commit_transaction(journal)
            hook=self.sync_boot_hook()
            return {'installed':True,'components':list(plan['components']),'bootstrap_directory':str(self.root/'bootstrap'),'automatic_updates':hook['enabled']}
        except BaseException:
            self.rollback(journal);raise

    def cleanup_created(self,item):
        info=self.inspect_container(item['container'])
        if info:
            source=next((mount['Source'] for mount in info.get('Mounts',[]) if mount['Destination']=='/data'),None)
            name='wg' if item['backend']=='wireguard' else 'awg'
            if info.get('Image')!=item['image'] or source!=item['data_directory'] or info.get('HostConfig',{}).get('NetworkMode')!='host' or info.get('Config',{}).get('Labels',{}).get('com.docker.compose.service')!=name+'-easy':
                raise ManagerError('Владение новым контейнером не подтверждено',73)
            self.run(['docker','stop',item['container']]);self.run(['docker','rm',item['container']])
        if self.run(['ip','link','show','dev',item['interface']],check=False) is not None:
            raise ManagerError('Новый интерфейс ещё активен; firewall и данные сохранены',75)
        if self.run(['nft','list','table','inet',item['table']],check=False) is not None:
            self.run(['nft','delete','table','inet',item['table']])
        markers={item['table']+suffix for suffix in ['_forward_v4','_return_v4','_forward_v6','_return_v6']}
        for family in ['ip','ip6']:
            raw=self.run(['nft','-j','list','chain',family,'filter','DOCKER-USER'],check=False)
            if not raw:continue
            for entry in json.loads(raw).get('nftables',[]):
                rule=entry.get('rule',{})
                owned_interface=any(expr.get('match',{}).get('right')==item['interface'] and expr.get('match',{}).get('left',{}).get('meta',{}).get('key') in ('iifname','oifname') for expr in rule.get('expr',[]))
                if rule.get('comment') in markers and owned_interface:
                    self.run(['nft','delete','rule',family,'filter','DOCKER-USER','handle',str(rule['handle'])])

    def extend(self,existing,args):
        self.install_stage('Проверка системы и добавление недостающего VPN')
        environment_check(run=lambda command:self.run(command))
        self.check_system_paths(existing)
        candidate=self.plan_install(args);selected=[name for name in candidate['components'] if name not in existing['components']]
        self.check_plan(candidate,existing)
        if args.build:self.build_images(args,selected)
        for name in selected:
            item=candidate['components'][name];item['image_ref']=self.resolve_image_reference(item['image_ref'])
            item['image']=self.image(item['image_ref'],item['backend'])
            self.validate_release_image(item,candidate['version'])
            directory=Path(item['data_directory'])
            if directory.exists() and any(directory.iterdir()):raise ManagerError('Каталог нового компонента уже занят',73)
        journal={'operation':'extend','phase':'prepared','previous':existing,'candidate':candidate,'selected':selected}
        self.save_journal(journal)
        try:
            for name in selected:self.initialize(name,candidate['components'][name],candidate)
            candidate['maintenance_components']=selected
            self.write_config(candidate)
            for name in selected:
                self.install_stage('Запуск '+name.upper()+' и ожидание готовности')
                self.compose('up','-d','--no-deps',name+'-easy');self.health(candidate['components'][name])
            self.register(candidate);journal['candidate']=copy.deepcopy(candidate);self.commit_transaction(journal)
            self.sync_boot_hook()
            return {'added':selected,'existing_components_unchanged':True}
        except BaseException:self.rollback(journal);raise

    def restore(self,args):
        manifest=verify_archive(args.file)
        previous=self.config(False)
        if previous: self.owned(previous)
        candidate=copy.deepcopy(previous or manifest['manager_config'])
        candidate['project']=str(self.project)
        selected=manifest['selected'] if args.component=='all' else [args.component]
        if any(name not in manifest['selected'] for name in selected): raise ManagerError('Компонент отсутствует в архиве',65)
        if not previous:
            self.check_system_paths()
            candidate['components']={name:copy.deepcopy(manifest['manager_config']['components'][name]) for name in selected}
            if self.legacy(): raise ManagerError('Сначала выполните контролируемую миграцию исходного AWG через install',65)
            for name,item in candidate['components'].items():
                item['data_directory']=str(Path(item['data_directory']).resolve())
                target=Path(item['data_directory'])
                if target.exists() and any(target.iterdir()): raise ManagerError('Каталог восстановления занят неизвестными данными',73)
        staged={}
        try:
            for name in selected:
                if name not in candidate['components']: raise ManagerError('Сначала установите компонент перед частичным восстановлением',65)
                item=candidate['components'][name]
                source=manifest['manager_config']['components'][name]
                data_directory=item['data_directory']
                item.update(copy.deepcopy(source));item['data_directory']=data_directory
                item['image']=self.image(source['image'],source['backend']); item['image_ref']=source.get('image_ref',source['image'])
                target=Path(item['data_directory'])
                stage=target.parent/('.restore-'+uuid.uuid4().hex)
                staged[name]=stage
                extract_component(args.file,name,stage)
                self.validate_state_image(item,stage)
            validate_instances(candidate['components'])
            self.check_plan(candidate,previous)
            if args.dry_run: return {'dry_run':True,'verified':True,'components':selected}
            journal={'operation':'restore','phase':'prepared','previous':previous,'candidate':candidate,'selected':selected,'source_file':str(Path(args.file).absolute())}
            try:
                if previous:
                    self.freeze(journal)
                    journal.update(snapshot=self.backup_selected(previous,selected)['file'],phase='snapshot_verified')
                self.save_journal(journal)
                working=copy.deepcopy(candidate);working['maintenance_components']=selected
                for name in selected:
                    if previous: self.compose('stop',name+'-easy')
                    target=Path(candidate['components'][name]['data_directory'])
                    old=target.parent/('.before-restore-'+uuid.uuid4().hex)
                    if target.exists(): durable_replace(target,old)
                    durable_replace(staged.pop(name),target)
                    self.write_config(working)
                    self.compose('up','-d','--no-deps',name+'-easy'); self.health(candidate['components'][name])
                if not previous: self.register(working)
                journal['candidate']=copy.deepcopy(working)
                self.commit_transaction(journal)
                if not previous:self.sync_boot_hook()
                return {'restored':selected,'verified':True}
            except BaseException:
                self.rollback(journal)
                raise
        finally:
            for directory in staged.values(): shutil.rmtree(directory,ignore_errors=True)

    def rollback(self,journal):
        self.install_stage('Восстановление после ошибки установки')
        if journal['operation']=='source-update':
            if journal.get('phase')!='prepared':raise ManagerError('Неизвестная фаза обновления исходников',65)
            self.owned(journal['previous'])
            self.journal.unlink(missing_ok=True);return
        if journal.get('phase')=='commit_uncertain':
            raise ManagerError('Не подтверждена запись точки фиксации; режим обслуживания сохранён. Исправьте ошибку хранения и выполните vpnctl recover',75)
        # register мог успеть сохранить undo-запись после последней фазы в памяти.
        if self.journal.exists():
            saved=json.loads(self.journal.read_text())
            if 'manager_files_before' in saved:journal['manager_files_before']=saved['manager_files_before']
        self.verify_transaction_containers(journal)
        if journal.get('phase')=='committed': return self.finish_committed(journal)
        if journal.get('phase')=='rollback_committed':return self.finish_rollback(journal)
        previous=journal.get('previous'); snapshot=journal.get('snapshot')
        if previous and not snapshot and journal['operation']=='install' and previous.get('legacy_origin'):
            legacy=previous['legacy_origin'];item=previous['components']['awg']
            if not self.inspect_container(item['container']):
                raise ManagerError('Исходный контейнер отсутствует до snapshot; автоматическое восстановление остановлено',75)
            if legacy.get('running'):
                self.run(['docker','start',item['container']]);self.health(item)
            self.journal.unlink(missing_ok=True);return
        if previous and not snapshot and journal['operation'] in ('update','restore','uninstall','reinstall'):
            self.commit_rollback(journal);return
        if (journal['operation']=='extend' and previous) or (journal['operation'] in ('install','restore') and not previous):
            for name in journal['selected']:
                item=journal['candidate']['components'][name]
                self.cleanup_created(item)
                directory=Path(item['data_directory'])
                if directory.exists():durable_replace(directory,directory.parent/('.failed-'+name+'-'+uuid.uuid4().hex))
            if previous:self.write_config(previous)
            else:
                self.path.unlink(missing_ok=True);self.compose_path.unlink(missing_ok=True)
            self.restore_manager_files(journal)
            self.journal.unlink(missing_ok=True);return
        if not previous or not snapshot: raise ManagerError('Нет совместимой резервной копии; требуется vpnctl recover',75)
        manifest=verify_archive(snapshot)
        for name in journal['selected']:
            candidate=journal['candidate']['components'].get(name) or previous['components'].get(name)
            if candidate and self.inspect_container(candidate['container']): self.run(['docker','stop',candidate['container']],check=False)
            if name not in manifest['selected']:
                if candidate:
                    self.cleanup_created(candidate)
                    directory=Path(candidate['data_directory'])
                    if directory.exists():durable_replace(directory,directory.parent/('.failed-'+name+'-'+uuid.uuid4().hex))
                continue
            target=Path(previous['components'][name]['data_directory'])
            stage=target.parent/('.rollback-'+uuid.uuid4().hex)
            extract_component(snapshot,name,stage)
            old=target.parent/('.failed-'+uuid.uuid4().hex)
            if target.exists(): durable_replace(target,old)
            durable_replace(stage,target)
        self.commit_rollback(journal)

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

    def update(self,args):
        config=self.config(); selected=self.selected(config,args.component)
        if not args.manifest:
            import vpnctl_source_update
            return vpnctl_source_update.update(self,args)
        try: release=json.loads(Path(args.manifest).read_text())
        except (OSError,ValueError): raise ManagerError('Не удалось прочитать манифест релиза',65) from None
        if not isinstance(release.get('version'),str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.[1-9]\d*)?',release['version']) or not release.get('images'): raise ManagerError('Некорректный манифест релиза',65)
        candidate=copy.deepcopy(config)
        for item in candidate['components'].values():item.setdefault('version',config['version'])
        candidate['version']=release['version']
        candidate['project']=str(self.project)
        if getattr(args,'source_commit',None):candidate['source_commit']=args.source_commit
        for name in selected:
            reference=release['images'].get(name)
            if not isinstance(reference,str) or ':latest' in reference: raise ManagerError('Релиз должен указывать точный образ компонента',65)
            candidate['components'][name]['image']=self.image(reference,candidate['components'][name]['backend'])
            candidate['components'][name]['image_ref']=reference
            self.validate_release_image(candidate['components'][name],release['version'])
            candidate['components'][name]['version']=release['version']
            self.validate_state_image(candidate['components'][name])
        if args.dry_run: return {'dry_run':True,'version':release['version'],'components':selected}
        self.owned(config)
        self.check_system_paths(config)
        journal={'operation':'update','phase':'prepared','previous':config,'candidate':candidate,'selected':selected}
        try:
            current=self.freeze(journal)
            snapshot=self.backup_selected(config,selected)['file']
            journal.update(snapshot=snapshot,phase='snapshot_verified');self.save_journal(journal)
            for name in selected:
                current['components'][name]=candidate['components'][name]
                self.write_config(current); self.compose('up','-d','--no-deps',name+'-easy'); self.health(current['components'][name])
                journal['phase']='updated:'+name; self.save_journal(journal)
            candidate['maintenance_components']=selected
            self.register(candidate)
            journal['candidate']=copy.deepcopy(candidate)
            self.commit_transaction(journal)
            self.sync_boot_hook()
            return {'updated':selected,'version':release['version'],'snapshot':snapshot}
        except BaseException:
            self.rollback(journal); raise

    @installation_progress
    def reinstall(self,args):
        self.install_stage('Проверка системы и текущей установки')
        previous=self.config(False)
        if not previous:return self.install(args)
        selected=self.selected(previous,args.component)
        self.owned(previous)
        environment_check(run=lambda command:self.run(command))
        self.check_system_paths(previous)
        remaining=copy.deepcopy(previous)
        for name in selected:del remaining['components'][name]
        if not remaining['components']:remaining={'components':{}}
        self.planning_existing=remaining
        self.reinstall_previous={'components':{name:item for name,item in previous['components'].items()
            if (self.inspect_container(item['container']) or {}).get('State',{}).get('Running')}}
        self.planning_targets=selected
        try:candidate=self.plan_install(args)
        finally:del self.planning_existing;del self.reinstall_previous;del self.planning_targets
        # Переустановка сохраняет расположение рабочих каталогов, но не их содержимое.
        for name in selected:
            if not args.data_root:candidate['components'][name]['data_directory']=previous['components'][name]['data_directory']
        selected=[name for name in candidate['components'] if name in selected]
        self.check_plan(candidate,previous)
        if args.build and not args.dry_run:self.build_images(args,selected)
        for name in selected:
            item=candidate['components'][name]
            if item['data_directory']!=previous['components'][name]['data_directory']:
                raise ManagerError('При переустановке нельзя менять каталог данных; используйте backup и restore',64)
            if not args.dry_run:
                item['image_ref']=self.resolve_image_reference(item['image_ref'])
                item['image']=self.image(item['image_ref'],item['backend'])
                self.validate_release_image(item,candidate['version'])
        if args.dry_run:return {'dry_run':True,'reinstall':selected,'backups_preserved':True}
        journal={'operation':'reinstall','phase':'prepared','previous':previous,'candidate':candidate,'selected':selected}
        journal['bootstrap_before']={name+suffix:base64.b64encode((self.root/'bootstrap'/(name+suffix)).read_bytes()).decode()
            for name in selected for suffix in ['.conf','.vpn-url'] if (self.root/'bootstrap'/(name+suffix)).is_file()}
        try:
            self.freeze(journal)
            snapshot=self.backup_selected(previous,selected)['file']
            journal.update(snapshot=snapshot,phase='snapshot_verified');self.save_journal(journal)
            for name in selected:
                self.cleanup_created(previous['components'][name])
                shutil.rmtree(previous['components'][name]['data_directory'])
                for suffix in ['.conf','.vpn-url']:(self.root/'bootstrap'/(name+suffix)).unlink(missing_ok=True)
                self.initialize(name,candidate['components'][name],candidate)
            candidate['maintenance_components']=selected
            self.write_config(candidate)
            for name in selected:
                self.install_stage('Запуск '+name.upper()+' и ожидание готовности')
                self.compose('up','-d','--no-deps',name+'-easy');self.health(candidate['components'][name])
            self.register(candidate);journal['candidate']=copy.deepcopy(candidate)
            self.commit_transaction(journal)
            self.sync_boot_hook()
            return {'reinstalled':selected,'snapshot':snapshot,'backups_preserved':True,'forwarding_values_preserved':True}
        except BaseException:self.rollback(journal);raise

    def finish_uninstall(self,journal):
        candidate=journal['candidate'];previous=journal['previous']
        for name in journal['selected']:
            for suffix in ['.conf','.vpn-url']:(self.root/'bootstrap'/(name+suffix)).unlink(missing_ok=True)
        if candidate['components']:
            candidate.pop('maintenance_components',None);self.write_config(candidate)
        else:
            try:vpnctl_boot.configure(self,False,previous)
            except ManagerError as error:
                if error.code!=73:raise
                print('Предупреждение: boot updater изменён другим приложением; его служба не изменена',file=sys.stderr)
            # Конфигурация не даёт права удалить произвольный путь с совпавшим hash.
            allowed={str(path) for path in {**self.system_files(),**vpnctl_boot.files(self.system_root,'systemd'),**vpnctl_boot.files(self.system_root,'openrc')}}
            for filename,digest in previous.get('manager_files',{}).items():
                if filename not in allowed:continue
                path=Path(filename)
                if path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest()==digest:path.unlink()
            self.path.unlink(missing_ok=True);self.compose_path.unlink(missing_ok=True)
            if (self.root/'bootstrap').exists():shutil.rmtree(self.root/'bootstrap')
        if journal.get('purge'):
            import vpnctl_purge
            return vpnctl_purge.finish(self,journal)
        self.journal.unlink(missing_ok=True)

    def purge(self,args):
        import vpnctl_purge
        return vpnctl_purge.purge(self,args)

    def uninstall(self,args):
        config=self.config(False)
        if not config: return {'unchanged':True,'installed':False}
        selected=self.selected(config,args.component); self.owned(config)
        if args.dry_run: return {'dry_run':True,'remove':selected}
        candidate=copy.deepcopy(config)
        journal={'operation':'uninstall','phase':'prepared','previous':config,'candidate':candidate,'selected':selected}
        if getattr(args,'purge',False):journal.update(purge=True,purge_images=args.purge_images)
        try:
            self.freeze(journal)
            snapshot=self.backup_selected(config,selected)['file']
            journal.update(snapshot=snapshot,phase='snapshot_verified');self.save_journal(journal)
            for name in selected:
                item=config['components'][name]
                self.compose('stop',name+'-easy'); self.compose('rm','-f',name+'-easy')
                if self.run(['ip','link','show','dev',item['interface']],check=False) is not None or self.run(['nft','list','table','inet',item['table']],check=False) is not None:
                    raise ManagerError('Сетевые ресурсы остались активны; данные сохранены',75)
                shutil.rmtree(item['data_directory']); del candidate['components'][name]
                journal['phase']='removed:'+name;self.save_journal(journal)
            journal['candidate']=candidate
            committed={**journal,'phase':'committed'};journal['phase']='commit_uncertain'
            self.save_journal(committed);journal.update(committed)
            result=self.finish_uninstall(journal)
            if journal.get('purge'):return result
            command=self.system_root/'usr/local/sbin/vpnctl'
            return {'removed':selected,'snapshot':snapshot,'forwarding_values_preserved':True,'backups_preserved':True,'vpnctl_removed':not command.exists() and not command.is_symlink()}
        except BaseException:
            if journal.get('purge') and journal.get('phase')=='committed':raise
            self.rollback(journal);raise

    def check_plan(self,candidate,previous=None):
        proof=self.owned(previous) if previous else {}
        return inspect_conflicts(candidate['components'],owned=proof,run=lambda command:self.run(command))

    def logs(self,component,tail=100,follow=False):
        config=self.config();selected=self.selected(config,component);self.owned(config)
        if not 0<=tail<=10000: raise ManagerError('Число строк журнала должно быть от 0 до 10000',64)
        if not follow:
            return {name:redact(self.run(['docker','logs','--tail',str(tail),config['components'][name]['container']],merge_stderr=True)) for name in selected}
        command=['docker','compose','--project-directory',str(self.project),'-f',str(self.compose_path),'logs','--no-color','--tail',str(tail),'--follow',*[name+'-easy' for name in selected]]
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        try:
            for line in process.stdout: print(redact(line),end='',flush=True)
        except KeyboardInterrupt: process.terminate()
        finally:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
        return None

def valid_port(value):
    if not str(value).isdecimal() or not 1<=int(value)<=65535:raise argparse.ArgumentTypeError('Порт должен быть от 1 до 65535')
    return int(value)

def build_parser():
    parser=Parser(prog='vpnctl',description='Единое управление AWG и стандартным WireGuard',
        epilog='По умолчанию — читаемый вывод и JSON-файл в /root; --json — JSON в stdout. Подробнее о параметрах: vpnctl КОМАНДА --help. Компонент all означает оба сервиса, awg — AmneziaWG, wg — WireGuard.')
    parser.add_argument('--config',default='/etc/vpn-easy-dual/config.json',help='Путь к конфигурации manager')
    sub=parser.add_subparsers(dest='command')
    def command(name,description,**kwargs):
        return sub.add_parser(name,help=description,description=description,**kwargs)
    command('help','Показать общую справку со списком команд')
    install_options=argparse.ArgumentParser(add_help=False)
    install=install_options
    install.add_argument('--host',default=os.environ.get('AWG_HOST'),help='Публичный IP или домен сервера')
    install.add_argument('--lang',choices=['en','ru','fa','es','zh-cn'],default=os.environ.get('AWG_LANG') or None)
    install.add_argument('--port','--awg-port',dest='awg_port',type=valid_port,default=os.environ.get('AWG_PORT'))
    install.add_argument('--wg-port',type=valid_port,default=os.environ.get('WG_PORT'))
    install.add_argument('--panel-port','--awg-panel-port',dest='awg_panel_port',type=valid_port,default=(os.environ.get('AWG_PANEL_PORT') or '51821') if 'AWG_PANEL_PORT' in os.environ else None)
    install.add_argument('--wg-panel-port',type=valid_port,default=os.environ.get('WG_PANEL_PORT'))
    install.add_argument('--data-root')
    install.add_argument('--awg-image',default=None)
    install.add_argument('--wg-image',default=None)
    install.add_argument('--build',action='store_true',help='Собрать оба локальных образа после preflight')
    install.add_argument('--awg-interface',default='awg0');install.add_argument('--wg-interface',default='wg0')
    install.add_argument('--awg-subnet',default='10.8.0.0/24');install.add_argument('--wg-subnet',default='10.9.0.0/24')
    actions=install.add_mutually_exclusive_group()
    actions.add_argument('--uninstall',action='store_true',help='Удалить выбранную установку')
    actions.add_argument('--reinstall',action='store_true',help='Выполнить чистую переустановку')
    install.add_argument('--yes',action='store_true');install.add_argument('--dry-run',action='store_true');install.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    for name in ['install','reinstall']:
        item=command(name,parents=[install_options],conflict_handler='resolve',description='Установка и дополнение Dual' if name=='install' else 'Чистая переустановка с новыми ключами')
        if name=='reinstall':item.add_argument('component',nargs='?',choices=['all','awg','wg'],default='all')
    descriptions={
        'status':'Показать состояние выбранных сервисов',
        'start':'Запустить выбранные сервисы',
        'stop':'Остановить выбранные сервисы',
        'restart':'Перезапустить выбранные сервисы',
        'settings':'Показать настройки выбранных сервисов',
        'logs':'Показать журналы выбранных сервисов',
        'diagnose':'Проверить состояние VPN; с --probe-profile проверить трафик клиента',
        'update':'Обновить исходники и готовые образы из последнего стабильного релиза с резервной копией и откатом',
        'backup':'Создать резервную копию выбранных сервисов',
        'uninstall':'Удалить выбранные сервисы и рабочие данные с подтверждением; сохранить внутренние backup',
    }
    for name,description in descriptions.items():
        item=command(name,description)
        item.add_argument('component',nargs='?',choices=['all','awg','wg'],default='all')
        item.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
        if name in ['update','backup','uninstall']: item.add_argument('--yes',action='store_true')
        if name in ['update','uninstall']: item.add_argument('--dry-run',action='store_true')
        if name=='update': item.add_argument('--manifest',help='Необязательно: обновить только образы по локальному манифесту')
        if name=='backup': item.add_argument('--output')
        if name=='logs': item.add_argument('--tail',type=int,default=100);item.add_argument('--follow',action='store_true')
        if name=='diagnose':
            item.add_argument('--probe-profile',help='Отдельный неиспользуемый профиль для пакетной проверки')
            item.add_argument('--peer-role',choices=['home','guest'],default='home')
            item.add_argument('--internet-url',default='https://example.com/')
            item.add_argument('--dns',action='store_true',help='Проверить DNS из профиля через VPN')

    purge=command('purge','Удалить все сервисы, рабочие данные, внутренние backup и установленный vpnctl; сохранить исходники')
    purge.add_argument('--yes',action='store_true',help='Явно подтвердить полное удаление')
    purge.add_argument('--dry-run',action='store_true',help='Показать план без удаления')
    purge.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    restore=command('restore',description='Восстановить проверенный архив')
    restore.add_argument('--file',required=True);restore.add_argument('--component',choices=['all','awg','wg'],required=True)
    restore.add_argument('--yes',action='store_true');restore.add_argument('--dry-run',action='store_true');restore.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    password=command('reset-password',description='Сбросить пароль и отозвать сессии')
    password.add_argument('--component',choices=['awg','wg'],required=True);password.add_argument('--password-stdin',action='store_true')
    client=command('client','Управлять клиентами: list, add, disable, enable, remove, export')
    commands=client.add_subparsers(dest='action',required=True)
    client_descriptions={
        'list':'Показать клиентов выбранных сервисов',
        'add':'Создать клиента группы home или guest',
        'disable':'Отключить клиента, сохранив его профиль',
        'enable':'Включить ранее отключённого клиента',
        'remove':'Удалить клиента с подтверждением',
        'export':'Экспортировать профиль: conf, QR-код или vpn:// для AWG',
    }
    for action,description in client_descriptions.items():
        item=commands.add_parser(action,help=description,description=description)
        item.add_argument('--component',choices=['all','awg','wg'] if action=='list' else ['awg','wg'],required=action!='list',default='all' if action=='list' else None)
        if action!='list': item.add_argument('--name',required=True)
        if action=='add': item.add_argument('--group',choices=['home','guest'],default='guest')
        if action=='remove': item.add_argument('--yes',action='store_true')
        if action=='export': item.add_argument('--format',choices=['conf','qr','vpn-url'],required=True);item.add_argument('--output')
        item.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    recover=command('recover',description='Восстановиться после прерванной операции')
    recover.add_argument('--yes',action='store_true');recover.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    auto=command('auto-update','Управлять проверкой и установкой stable-релиза после загрузки ОС')
    auto.add_argument('action',choices=['enable','disable','status','run'],
        help='enable — включить; disable — отключить; status — показать состояние; run — проверить stable-релиз и применить обновление')
    auto.add_argument('--json',action='store_true',help='Вывести JSON в stdout вместо читаемого результата и файла отчёта')
    return parser

def confirmation(args,message):
    if getattr(args,'yes',False) or getattr(args,'dry_run',False): return
    if not sys.stdin.isatty(): raise ManagerError('Требуется подтверждение: '+message+'. Для автоматизации укажите --yes',64)
    while True:
        try:answer=ask(message+' Продолжить? [y/N]: ').strip().casefold()
        except EOFError:raise ManagerError('Операция отменена',64) from None
        if answer in ('y','yes','да','д'):return
        if answer in ('','n','no','нет','н'):raise ManagerError('Операция отменена',64)
        print('Введите yes или no',file=sys.stderr)

def menu(manager):
    print('Состояние сервисов:\n'+format_human(manager.status(),2))
    choices={'1':['status'],'2':['start'],'3':['stop'],'4':['restart'],'5':['settings'],'6':['client','list'],
      '7':['backup'],'8':['diagnose'],'9':['logs'],'10':['update'],'11':['restore'],'12':['reset-password'],'13':['uninstall'],'14':['reinstall'],'15':['purge']}
    print('1 Состояние; 2 Запуск; 3 Остановка; 4 Перезапуск; 5 Настройки; 6 Клиенты;\n7 Backup; 8 Диагностика; 9 Журналы; 10 Update; 11 Restore; 12 Пароль; 13 Удаление; 14 Чистая переустановка; 15 Полное удаление, включая backup; 0 Выход')
    choice=ask('Действие: ').strip()
    if choice=='0': return None
    if choice not in choices: raise ManagerError('Неизвестный пункт меню',64)
    command=choices[choice]
    if command[0]=='purge':return command
    if command[0]=='restore': return command+['--file',ask('Путь к архиву: ').strip(),'--component',ask('Компонент awg/wg/all: ').strip()]
    if command[0]=='reset-password': return command+['--component',ask('Компонент awg/wg: ').strip()]
    if command[0]=='update': return command
    if command[0]=='client':
        action=ask('list/add/disable/enable/remove/export: ').strip() or 'list'
        target=ask('Компонент awg/wg'+('/all' if action=='list' else '')+': ').strip()
        command=['client',action,'--component',target]
        if action!='list': command+=['--name',ask('Имя клиента: ').strip()]
        if action=='add': command+=['--group',ask('Группа home/guest: ').strip()]
        if action=='export': command+=['--format',ask('Формат conf/qr/vpn-url: ').strip(),'--output',ask('Файл профиля: ').strip()]
        return command
    return command+[ask('Компонент all/awg/wg: ').strip() or 'all']

def parse_arguments(project,argv):
    values=list(sys.argv[1:] if argv is None else argv)
    validate_only=bool(values and values[0]=='--validate-only')
    if validate_only:values.pop(0)
    prefix=[]
    while values and (values[0]=='--config' or values[0].startswith('--config=')):
        flag=values.pop(0);prefix.append(flag)
        if flag=='--config' and values:prefix.append(values.pop(0))
    if values and values[0].startswith('--') and values[0]!='--help':values.insert(0,'install')
    parser=build_parser();args=parser.parse_args(prefix+values)
    if not Path(args.config).is_absolute():args.config=str((Path(project)/args.config).resolve())
    if args.command in ('install','reinstall'):
        if args.lang is not None and args.lang not in ('en','ru','fa','es','zh-cn'):parser.error('Некорректный язык')
        if getattr(args,'uninstall',False):args.command='uninstall';args.component='all'
        elif getattr(args,'reinstall',False):args.command='reinstall';args.component='all'
        if args.command in ('install','reinstall'):
            for name in ('awg','wg'):
                if getattr(args,name+'_image') is None:
                    if args.build:setattr(args,name+'_image','vpn-easy-'+name+':dual-dev')
                    else:
                        version=(Path(project)/'VERSION').read_text().strip()
                        setattr(args,name+'_image','release://'+version+'/'+name)
            if args.data_root:args.data_root=str((Path(project)/args.data_root).resolve())
    return parser,args,validate_only

def main(project,argv=None):
    try:parser,args,validate_only=parse_arguments(project,argv)
    except (OSError,ValueError,KeyError):
        print('Ошибка: не удалось прочитать версию исходников vpnctl',file=sys.stderr);return 65
    if validate_only:return 0
    if args.command=='help': parser.print_help();return 0
    if os.geteuid()!=0: print('Ошибка: запустите vpnctl через sudo',file=sys.stderr);return 69
    manager=Manager(project,args.config)
    if not args.command:
        if not sys.stdin.isatty(): parser.print_help();return 64
        try: chosen=menu(manager)
        except ManagerError as error: print('Ошибка: '+str(error),file=sys.stderr);return error.code
        return main(project,['--config',args.config,*chosen]) if chosen else 0
    readonly=args.command in ('status','settings','logs','diagnose') or args.command=='client' and args.action in ('list','export') or args.command=='auto-update' and args.action=='status'
    dry=getattr(args,'dry_run',False)
    try:
        if manager.journal.exists() and not readonly and args.command not in ('recover','stop'):
            raise ManagerError('Обнаружена прерванная операция; выполните vpnctl recover',75)
        if args.command=='purge':
            confirmation(args,'Будут удалены ВСЕ сервисы приложения, клиенты, ключи, пароли, настройки, внутренние backup и установленный vpnctl. Восстановление из внутренних backup станет невозможно. Исходники, Docker, чужие ресурсы и текущий forwarding сохраняются')
        elif args.command in ('uninstall','reinstall'):
            confirmation(args,'Будут удалены рабочие клиенты, ключи, пароль, настройки и bootstrap-профили выбранных VPN. Внутренние backup со старыми ключами сохраняются. Docker, чужие ресурсы и текущий forwarding сохраняются. После удаления последнего VPN установленная команда vpnctl удаляется')
        elif args.command in ('backup','restore','recover') or args.command=='client' and args.action=='remove':
            confirmation(args,'Операция может остановить выбранные VPN или заменить данные')
        if args.command=='install' and manager.config(False) and set(manager.config()['components'])=={'awg','wg'}:
            if not sys.stdin.isatty():raise ManagerError('Установка уже существует. Используйте vpnctl status, uninstall или reinstall; для удаления данных требуется --yes',64)
            print('Установка уже существует:\n1 Сохранить и выйти (по умолчанию)\n2 Удалить\n3 Чистая переустановка',file=sys.stderr)
            try:choice=ask('Действие [1]: ').strip() or '1'
            except EOFError:choice='1'
            if choice=='1':return emit_result(manager,args,{'installed':True,'unchanged':True,'components':list(manager.config()['components'])})
            if choice not in ('2','3'):raise ManagerError('Неизвестный пункт меню; установка не изменена',64)
            args.command='uninstall' if choice=='2' else 'reinstall';args.component='all'
            confirmation(args,'Будут удалены рабочие клиенты, ключи, пароль и настройки; внутренние backup сохраняются')
        if args.command=='install' and not manager.config(False) and manager.legacy():
            confirmation(args,'Миграция кратковременно остановит исходный AWG; сначала будет создана резервная копия')
        def perform():
            if args.command=='auto-update':
                if args.action=='run':return vpnctl_boot.run(manager)
                config=manager.config()
                if args.action=='status':return vpnctl_boot.status(manager)
                manager.check_system_paths(config)
                config['auto_check']=args.action=='enable';manager.register(config)
                return vpnctl_boot.configure(manager,config['auto_check'])
            if args.command=='status': return manager.status(args.component)
            if args.command in ('start','stop','restart'): return manager.lifecycle(args.command,args.component)
            if args.command=='install':
                if dry:
                    environment_check(run=lambda command:manager.run(command));plan=manager.plan_install(args)
                    legacy=plan.get('legacy_origin')
                    current=manager.config(False)
                    proof=manager.owned(current) if current else {'awg':legacy['item']} if legacy else {}
                    inspect_conflicts(plan['components'],owned=proof,run=lambda command:manager.run(command))
                    return {'dry_run':True,'components':plan['components']}
                return manager.install(args)
            if args.command=='reinstall':return manager.reinstall(args)
            if args.command=='diagnose': return manager.diagnose(args.component,args)
            if args.command=='backup': return manager.backup(args.component,args.output)
            if args.command=='restore': return manager.restore(args)
            if args.command=='update': return manager.update(args)
            if args.command=='purge':return manager.purge(args)
            if args.command=='uninstall': return manager.uninstall(args)
            if args.command=='recover':
                if not manager.journal.exists(): return {'recovery_required':False}
                journal=json.loads(manager.journal.read_text());manager.rollback(journal)
                return {'recovered':True,'purged':bool(journal.get('purge') and journal.get('phase')=='committed')}
            config=manager.config()
            selected=manager.selected(config,args.component)
            if args.command=='settings': return {name:manager.rpc(config['components'][name],'settings') for name in selected}
            if args.command=='logs':
                return manager.logs(args.component,args.tail,args.follow)
            if args.command=='reset-password':
                options={}
                if args.password_stdin: options['password']=sys.stdin.read().rstrip('\n')
                return manager.rpc(config['components'][args.component],'reset-password',options)
            if args.command=='client':
                options={key:getattr(args,key) for key in ('name','group','format') if hasattr(args,key)}
                result={name:manager.rpc(config['components'][name],'client.'+args.action,options) for name in selected}
                if args.action=='export':
                    value=result[args.component]['value']
                    if args.output:
                        if Path(args.output).exists(): raise ManagerError('Файл экспорта уже существует',73)
                        atomic_write(args.output,value);return {'file':str(Path(args.output).absolute())}
                    return {'export_value':value}
                return result
            raise ManagerError('Неизвестная команда',64)
        if readonly or dry: result=perform()
        else:
            with global_lock(recovery_journal=manager.journal if args.command=='recover' else None): result=perform()
        if not readonly and not dry and not (isinstance(result,dict) and result.get('purged')):
            try: manager.log_result(args.command,'ok')
            except OSError: print('Предупреждение: операция завершена, но журнал записать не удалось',file=sys.stderr)
        if result is None: return 0
        if isinstance(result,dict) and 'export_value' in result: sys.stdout.write(result['export_value']);return 0
        if args.command=='reset-password': print('Новый пароль (показан один раз): '+result['password']);return 0
        try:return emit_result(manager,args,result)
        except (ManagerError,OSError) as error:
            print('Команда выполнена, но JSON-результат не сохранён: '+str(error),file=sys.stderr)
            return getattr(error,'code',74)

    except (ManagerError,PreflightError) as error:
        if not readonly and not dry and args.command!='purge':
            try: manager.log_result(args.command,{'status':'failed','code':getattr(error,'code',73),'recovery_required':manager.journal.exists()})
            except OSError: pass
        print('Ошибка: '+str(error),file=sys.stderr);return getattr(error,'code',73)
    except KeyboardInterrupt:
        print('Операция прервана; при наличии transaction.json выполните vpnctl recover',file=sys.stderr);return 130
    except (OSError,ValueError,KeyError,TypeError):
        print('Ошибка: операция не завершена; проверьте vpnctl status и журнал транзакции',file=sys.stderr);return 74

if __name__=='__main__':
    def interrupted(_number,_frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    raise SystemExit(main(Path(__file__).resolve().parents[1]))

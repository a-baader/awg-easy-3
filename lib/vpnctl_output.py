"""Читаемый вывод CLI и JSON-отчёты без сохранения секретов."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re

from vpnctl_terminal import paint
from vpnctl_common import ManagerError,atomic_json

LABELS={
    'installed':'Установлено','unchanged':'Без изменений','components':'Сервисы','version':'Версия',
    'container':'Контейнер','running':'Запущен','health':'Готовность','image':'Образ','image_ref':'Ссылка на образ',
    'interface':'Интерфейс','panel_url':'Адрес панели','recovery_required':'Требуется восстановление',
    'updated':'Обновлено','reason':'Причина','snapshot':'Резервная копия','file':'Файл','verified':'Проверено',
    'added':'Добавлено','remaining_components':'Оставшиеся сервисы','removed':'Удалено','reinstalled':'Переустановлено','restored':'Восстановлено',
    'purged':'Полное удаление выполнено','sources_preserved':'Исходники сохранены',
    'backups_preserved':'Внутренние backup сохранены','forwarding_values_preserved':'Forwarding сохранён',
    'vpnctl_removed':'Установленный vpnctl удалён','images_removed':'Удалённые образы',
    'images_preserved':'Сохранённые образы','preserved_files':'Сохранённые файлы',
    'automatic_updates':'Проверка обновлений после загрузки','bootstrap_directory':'Каталог первых профилей',
    'existing_components_unchanged':'Существующие сервисы сохранены','source_commit':'Commit исходников',
    'sources_directory':'Каталог исходников','previous_sources_preserved':'Предыдущие исходники сохранены',
    'dry_run':'Только проверка и план','update_sources':'Обновление исходников','images':'Образы',
    'profile_file':'Файл профиля','udp_port':'VPN (UDP)','panel_port':'Панель (TCP)','name':'Имя','id':'Идентификатор',
    'clients':'Клиенты','group':'Группа','enabled':'Включено','backend':'VPN backend','requested':'Запрошено',
    'skipped':'Пропущено','status':'Состояние','result':'Результат','success':'Успешно',
    'awg':'AWG','wg':'WG','all':'Все сервисы','remove':'К удалению','reinstall':'К переустановке',
    'remove_internal_directories':'Удаляемые внутренние каталоги','image_candidates':'Образы для проверки удаления',
    'installed_command_removal_requested':'Запрошено удаление установленного vpnctl',
}
COMMANDS={'install':'Установка','reinstall':'Переустановка','status':'Состояние сервисов',
          'start':'Запуск','stop':'Остановка','restart':'Перезапуск','settings':'Настройки',
          'logs':'Журналы','diagnose':'Диагностика','update':'Обновление','backup':'Резервная копия',
          'restore':'Восстановление','uninstall':'Удаление','purge':'Полное удаление',
          'recover':'Восстановление после прерывания','client':'Управление клиентами','auto-update':'Проверка обновлений'}


def format_human(value,indent=0):
    prefix=' '*indent
    def scalar(item):
        if item is True:return paint('да','success')
        if item is False:return paint('нет','warning')
        if item is None:return 'не указано'
        return paint(str(item),'success') if item in ('healthy','running','ok') else paint(str(item),'error') if item in ('unhealthy','error','failed') else str(item)
    if isinstance(value,dict):
        if not value:return prefix+'нет данных'
        lines=[]
        for key,item in value.items():
            label=paint(LABELS.get(str(key),str(key)),'label')
            if isinstance(item,(dict,list)):
                lines.append(prefix+label+':');lines.append(format_human(item,indent+2))
            elif isinstance(item,str) and '\n' in item:
                lines.append(prefix+label+':');lines.extend(' '*(indent+2)+line for line in item.splitlines())
            else:lines.append(prefix+label+': '+scalar(item))
        return '\n'.join(lines)
    if isinstance(value,list):
        if not value:return prefix+'нет'
        return '\n'.join(prefix+str(number)+'.\n'+format_human(item,indent+2) if isinstance(item,(dict,list)) else prefix+'- '+scalar(item)
                         for number,item in enumerate(value,1))
    return prefix+scalar(value)


def public_value(value):
    # Структурные секреты удаляются рекурсивно, включая вложенные ответы RPC.
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            normalized=re.sub('[^a-z0-9]','',str(key).lower())
            if any(word in normalized for word in ['password','privatekey','presharedkey','secret','token','cookie','authorization']):continue
            if normalized in ('vpnurl','vpnlink','nativeconfig','nativeconfiguration','exportvalue'):continue
            result[key]=public_value(item)
        return result
    if isinstance(value,list):return [public_value(item) for item in value]
    if isinstance(value,str):
        if re.search(r'privatekey\s*["\']?\s*[:=]',value,re.I):return '[Содержимое профиля не сохраняется]'
        return re.sub(r'vpn://[^\s"\']+','[Ссылка профиля не сохраняется]',value,flags=re.I)
    return value


def result_path(manager,args):
    name='install' if args.command in ('install','reinstall') else args.command
    if args.command in ('client','auto-update'):name+='-'+args.action
    return manager.system_root/'root'/('wg-dual-easy-'+name+'-result.json')


def public_result(manager,args,result):
    report={'application':'wg-dual-easy','report_version':1,'command':args.command,'success':True,
            'created_at':datetime.now(timezone.utc).isoformat(),'result':public_value(result)}
    if getattr(args,'action',None):report['action']=args.action
    if args.command in ('install','reinstall'):
        report['panels']=installation_connections(manager)
    return report


def installation_connections(manager):
    # Фактические порты берутся из сохранённой конфигурации после установки.
    panels={}
    config=manager.config(False)
    for name,item in (config or {}).get('components',{}).items():
        initial=manager.bootstrap_results.get(name,{})
        panels[name]={'udp_port':item['udp_port'],'panel_port':item['panel_port'],
                      'panel_url':initial.get('panel_url') or 'http://'+item['address4']+':'+str(item['panel_port']),
                      'profile_file':initial.get('profile_file') or str(manager.root/'bootstrap'/(name+'.conf'))}
    return panels


def write_result(report,path):
    path=Path(path)
    if path.is_symlink():raise ManagerError('Файл результата является символьной ссылкой; он не изменён',73)
    if path.exists():
        if not path.is_file():raise ManagerError('Путь результата занят другим объектом',73)
        try:previous=json.loads(path.read_text())
        except (OSError,ValueError):raise ManagerError('Существующий файл результата не принадлежит приложению; он сохранён',73) from None
        if not isinstance(previous,dict) or previous.get('application')!='wg-dual-easy' or previous.get('report_version')!=1:
            raise ManagerError('Существующий файл результата не принадлежит приложению; он сохранён',73)
    atomic_json(path,report)
    return path


def print_bootstrap(manager):
    for name,initial in manager.bootstrap_results.items():
        print('\n'+paint(name.upper()+': панель после подключения VPN ','heading')+initial['panel_url'])
        print(paint('Пароль панели (показан один раз): ','label')+initial['password'])
        print(paint('Профиль .conf: ','label')+initial['profile_file'])
        if initial.get('vpn_url'):print(paint('Первый профиль AmneziaVPN (показан один раз):','label')+'\n'+initial['vpn_url'])


def emit_result(manager,args,result):
    if getattr(args,'json',False):
        if manager.bootstrap_results:result={**result,'bootstrap':manager.bootstrap_results}
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    title='Итоговый отчёт об удалении' if args.command in ('uninstall','purge') and not getattr(args,'dry_run',False) else COMMANDS.get(args.command,args.command)
    print(paint(title+':','heading'))
    print(format_human(result,2))
    if args.command in ('install','reinstall') and not getattr(args,'dry_run',False):
        connections=installation_connections(manager)
        if connections:
            print('\n'+paint('Порты установленных сервисов:','heading'))
            for name,item in connections.items():
                print('  '+paint(name.upper(),'heading'))
                print(format_human({'udp_port':item['udp_port'],'panel_port':item['panel_port']},4))
    # Секреты показываются до записи отчёта: ошибка диска не должна скрыть
    # однократный пароль уже успешно созданной панели.
    print_bootstrap(manager)
    path=write_result(public_result(manager,args,result),result_path(manager,args))
    print('\n'+paint('JSON-результат сохранён: ','success')+str(path))
    return 0

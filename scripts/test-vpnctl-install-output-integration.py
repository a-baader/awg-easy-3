"""Прогресс, текстовый результат, приватный отчёт и JSON настоящей установки."""
from vpnctl_disposable import (IsolatedManager,root,prefix,anchor,images,PROJECT,
    execute,build_parser,mock,shutil,passed)
from contextlib import nullcontext
import io
import json
from pathlib import Path
import vpnctl_manager
from vpnctl_output import result_path

manager=IsolatedManager()
def invoke(target,arguments):
    out=io.StringIO();err=io.StringIO()
    with mock.patch('vpnctl_manager.Manager',return_value=target),mock.patch('vpnctl_manager.environment_check'),mock.patch('vpnctl_manager.global_lock',return_value=nullcontext()),mock.patch('sys.stdout',out),mock.patch('sys.stderr',err):
        code=vpnctl_manager.main(PROJECT,arguments)
    assert code==0,'Команда не завершилась успешно, код '+str(code)
    return out.getvalue(),err.getvalue()
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    install=['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg']]
    text,progress=invoke(manager,install)
    assert text.startswith('Установка:') and 'Установлено: да' in text
    assert all(stage in progress for stage in ['Проверка системы','Запуск AWG','Запуск WG','Установка завершена'])
    report=result_path(manager,build_parser().parse_args(install));saved=report.read_bytes()
    payload=json.loads(saved);assert payload['success'] and set(payload['panels'])=={'awg','wg'}
    assert report.stat().st_mode&0o777==0o600
    for name,initial in manager.bootstrap_results.items():
        assert initial['password'] in text and initial['password'] not in progress
        assert initial['password'].encode() not in saved
        assert payload['panels'][name]['panel_url']==initial['panel_url']
        assert payload['panels'][name]['profile_file']==initial['profile_file']
        if initial.get('vpn_url'):assert initial['vpn_url'] in text and initial['vpn_url'].encode() not in saved
    for item in manager.config()['components'].values():
        assert 'VPN (UDP): '+str(item['udp_port']) in text
        assert 'Панель (TCP): '+str(item['panel_port']) in text
    assert all(manager.inspect_container(item['container'])['State']['Health']['Status']=='healthy' for item in manager.config()['components'].values())
    passed('обычная установка: реальные этапы в stderr, читаемый результат и пароли в терминале; JSON-файл 0600 без паролей и vpn://')
    fresh=IsolatedManager();text,progress=invoke(fresh,['status','--json'])
    assert json.loads(text)['installed'];assert not result_path(fresh,build_parser().parse_args(['status'])).exists()
    passed('status --json: только один JSON в stdout, отдельный файл не создаётся')
    manager.uninstall(build_parser().parse_args(['uninstall','all','--yes']))
    manager=IsolatedManager();text,progress=invoke(manager,install+['--json'])
    payload=json.loads(text);assert payload['installed'] and set(payload['bootstrap'])=={'awg','wg'}
    assert report.read_bytes()==saved
    for initial in payload['bootstrap'].values():assert initial['password'] not in progress
    assert 'Установка завершена' in progress
    passed('install --json: один JSON с bootstrap в stdout, прогресс отдельно; прежний публичный отчёт не изменён')
finally:
    for name in [prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

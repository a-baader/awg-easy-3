"""Браузерная проверка настоящих панелей через Home VPN peer и ограниченный proxy."""
from vpnctl_disposable import (IsolatedManager, root, prefix, anchor, images, PROJECT,
    execute, build_parser, mock, shutil)
import json
import os
from pathlib import Path
import subprocess
import time
manager=IsolatedManager();peers=[]
try:
    execute(['docker','run','-d','--name',anchor,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
        '--sysctl','net.ipv4.ip_forward=1','--sysctl','net.ipv6.conf.all.forwarding=1','--entrypoint','sleep',images['wg'],'infinity'])
    host=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',anchor]).strip()
    args=build_parser().parse_args(['install','--host',host,'--data-root',str(root/'data'),'--awg-image',images['awg'],'--wg-image',images['wg'],'--yes'])
    with mock.patch('vpnctl_manager.environment_check',return_value={'fixture':'disposable'}):manager.install(args)
    targets=[]
    for name,item in manager.config()['components'].items():
        native=manager.rpc(item,'client.export',{'name':'admin','format':'conf'})['value']
        profile=Path(item['data_directory'])/'profile-0.conf';profile.write_text(native);profile.chmod(0o600)
        peer=prefix+'-'+name+'-peer';peers.append(peer)
        execute(['docker','run','-d','--name',peer,'--network','bridge','--cap-add','NET_ADMIN','--device','/dev/net/tun',
            '--sysctl','net.ipv6.conf.all.disable_ipv6=0','--read-only','--tmpfs','/run:mode=0700','--tmpfs','/tmp:mode=1777',
            '-e','VPN_BACKEND='+item['backend'],'-v',item['data_directory']+':/data:ro',
            '-v',str(PROJECT/'scripts/dual-network-helper.cjs')+':/test.cjs:ro',
            '-v',str(PROJECT/'scripts/browser/proxy.cjs')+':/proxy.cjs:ro','--entrypoint','node',images[name],'/test.cjs','client','0'])
        ready=False
        for _ in range(80):
            if 'Клиент готов.' in execute(['docker','logs',peer]):ready=True;break
            time.sleep(.1)
        assert ready,'Тестовый Home peer не запущен'
        url=f"http://{item['address4']}:{item['panel_port']}"
        execute(['docker','exec','-d',peer,'node','/proxy.cjs',url])
        address=execute(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',peer]).strip()
        targets.append({'backend':name,'url':url,'proxy':'http://'+address+':3128','password':manager.bootstrap_results[name]['password']})
    options={'targets':targets,'artifacts':os.environ.get('BROWSER_ARTIFACT_DIR',str(root/'browser-artifacts'))}
    node=os.environ.get('BROWSER_NODE','node')
    result=subprocess.run([node,str(PROJECT/'scripts/browser/check.cjs')],input=json.dumps(options),text=True,timeout=240)
    assert result.returncode==0,'Браузерная проверка завершилась с ошибкой'
    for name,item in manager.config()['components'].items():
        binary='wg' if name=='wg' else 'awg'
        transfer=execute(['docker','exec',item['container'],binary,'show',item['interface'],'transfer']).splitlines()
        assert any(len(row.split())==3 and int(row.split()[1])>0 and int(row.split()[2])>0 for row in transfer),'Не подтверждён шифрованный трафик браузера'
    print('PASS: HTTP браузера прошёл через настоящий Home VPN обоих backend',flush=True)
except BaseException:
    from vpnctl_common import redact
    for name in peers:
        logs=execute(['docker','logs',name],check=False,merge_stderr=True)
        if logs:print(redact(logs[-2500:]),flush=True)
    raise
finally:
    for name in [*peers,prefix+'-awg',prefix+'-wg',anchor]:execute(['docker','rm','-f','-v',name],check=False)
    shutil.rmtree(root,ignore_errors=True)

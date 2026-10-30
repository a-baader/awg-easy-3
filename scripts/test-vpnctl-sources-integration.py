"""Установленный launcher проверяет доверие до запуска кода из снимка."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import Manager

with tempfile.TemporaryDirectory(prefix='vpnctl-trusted-launcher-') as directory:
    root=Path(directory);manager=Manager(PROJECT,root/'manager/config.json',system_root=root/'system')
    target=manager.install_sources()
    marker=root/'marker';marker.write_text('/source\n');marker.chmod(0o644)
    command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '-v',str(target)+':/source:ro','-v',str(target/'vpnctl')+':/usr/local/sbin/vpnctl:ro',
        '-v',str(marker)+':/etc/vpn-easy-dual-install-dir:ro',
        '--entrypoint','/usr/local/sbin/vpnctl',os.environ.get('TEST_WG_IMAGE','vpn-easy-wg:dual-stage5'),'--help']
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0 and 'vpnctl' in result.stdout,result.stderr
    (target/'lib').chmod(0o777)
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=30)
        assert result.returncode==73 and 'небезопасные права' in result.stderr,result.stderr
    finally:(target/'lib').chmod(0o755)
print('PASS: защищённый установленный launcher работает; writable lib отвергается до Python/shell import')

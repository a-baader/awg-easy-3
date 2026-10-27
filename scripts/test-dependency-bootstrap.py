"""Проверка декларативной ветки NixOS без установки пакетов на хосте."""
import os
from pathlib import Path
import subprocess
import tempfile
project=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='vpnctl-nixos-') as directory:
    release=Path(directory)/'os-release';release.write_text('ID=nixos\nNAME=NixOS\n')
    result=subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '-v',str(release)+':/etc/os-release:ro','-v',str(project/'lib/dual-dependencies.sh')+':/bootstrap.sh:ro',
        '--entrypoint','sh',os.environ.get('TEST_WG_IMAGE','vpn-easy-wg:dual-stage5'),'/bootstrap.sh','--yes'],capture_output=True,text=True,timeout=30)
    assert result.returncode==69,result.stderr
    for expected in ['vpn-easy-dual-runtime.nix','imports','nixos-rebuild switch --option max-jobs 1 --option cores 1','configuration.nix автоматически не изменяется']:
        assert expected in result.stderr,expected
print('PASS: NixOS выводит отдельный модуль и ограниченную команду rebuild; установка пакетов не запускается')

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

# Выполняется весь bootstrap с подставным pacman внутри одноразового контейнера.
# Реальные package database и зависимости хоста не изменяются.
with tempfile.TemporaryDirectory(prefix='vpnctl-arch-') as directory:
    root=Path(directory);tools=root/'tools';tools.mkdir();output=root/'output';output.mkdir();output.chmod(0o777)
    for name in ['id','uname','grep']:(tools/name).symlink_to('/bin/busybox')
    for name in ['ip','ss','nft']:
        (tools/name).write_text('#!/bin/sh\nexit 0\n');(tools/name).chmod(0o755)
    (tools/'docker').write_text('#!/bin/sh\nif [ "$1" = compose ]; then [ -e /out/installed ]; else exit 0; fi\n')
    (tools/'docker').chmod(0o755)
    (tools/'pacman').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> /out/pacman.args\n[ "${PACMAN_FAIL:-0}" = 0 ] || exit 76\n/bin/busybox touch /out/installed\n')
    (tools/'pacman').chmod(0o755)
    for fail in [False,True]:
        (output/'installed').unlink(missing_ok=True)
        result=subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--device','/dev/net/tun',
            '-e','PATH=/fake','-e','PACMAN_FAIL='+('1' if fail else '0'),
            '-v',str(tools)+':/fake:ro','-v',str(output)+':/out',
            '-v',str(project/'lib/dual-dependencies.sh')+':/bootstrap.sh:ro',
            '--entrypoint','/bin/sh',os.environ.get('TEST_WG_IMAGE','vpn-easy-wg:dual-stage5'),'/bootstrap.sh','--yes'],capture_output=True,text=True,timeout=30)
        assert result.returncode==(69 if fail else 0),result.stderr
        if fail:assert 'sudo pacman -Syu' in result.stderr
    calls=(output/'pacman.args').read_text().splitlines()
    assert len(calls)==2 and all('--noconfirm -S --needed' in call and '-Sy' not in call for call in calls),calls
print('PASS: Arch не обновляет package database отдельно; сбой требует явного полного обновления администратором')

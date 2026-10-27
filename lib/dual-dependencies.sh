#!/bin/sh
# Только подготовка зависимостей. VPN-ресурсы и пользовательские данные не меняются.
set -eu
fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 69; }
has() { command -v "$1" >/dev/null 2>&1; }
[ "$(id -u)" -eq 0 ] || fail 'запустите установку через sudo'
[ "$(uname -s)" = Linux ] || fail 'требуется нативный Linux'
case "$(uname -m)" in x86_64|amd64) ;; *) fail 'поддерживается Linux/amd64' ;; esac
case "$(uname -r)" in *[Mm]icrosoft*|*[Ww][Ss][Ll]*) fail 'WSL не поддерживается' ;; esac
if grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease /proc/version 2>/dev/null; then fail 'WSL не поддерживается'; fi
need_docker=0; need_compose=0; need_iproute=0; need_nft=0; need_python=0
has docker || need_docker=1
has python3 || need_python=1
has ip && has ss || need_iproute=1
has nft || need_nft=1
if [ "$need_docker" -eq 1 ] || ! docker compose version >/dev/null 2>&1; then need_compose=1; fi
if [ "$need_docker$need_compose$need_iproute$need_nft$need_python" != 00000 ]; then
  if [ -r /etc/os-release ]; then . /etc/os-release; else ID=unknown; fi
  if [ "${ID:-}" = nixos ]; then
    cat >&2 <<'NIX'
Ошибка: на NixOS зависимости настраиваются декларативно.
Создайте /etc/nixos/vpn-easy-dual-runtime.nix:
{ pkgs, ... }: {
  virtualisation.docker.enable = true;
  environment.systemPackages = with pkgs; [ docker-compose iproute2 nftables python3 ];
  boot.kernelModules = [ "tun" ];
}
Добавьте ./vpn-easy-dual-runtime.nix в imports файла configuration.nix.
Примените: sudo nixos-rebuild switch --option max-jobs 1 --option cores 1
Затем повторите sudo ./vpnctl install. configuration.nix автоматически не изменяется.
NIX
    exit 69
  fi
  if [ "${1:-}" != --yes ] && [ -t 0 ]; then
    printf 'Установить недостающие Docker, Compose v2, iproute2, nftables и Python 3? [Y/n]: '
    read -r answer
    case "$answer" in n|N|no|нет|Нет) fail 'установка зависимостей отменена' ;; esac
  fi
  manager=
  for candidate in apt-get dnf yum zypper pacman apk; do if has "$candidate"; then manager=$candidate; break; fi; done
  [ -n "$manager" ] || fail 'менеджер пакетов не поддерживается; установите зависимости вручную'
  packages=
  case "$manager" in
    apt-get)
      apt-get update
      [ "$need_docker" -eq 0 ] || packages="$packages docker.io"
      [ "$need_iproute" -eq 0 ] || packages="$packages iproute2"
      [ "$need_nft" -eq 0 ] || packages="$packages nftables"
      [ "$need_python" -eq 0 ] || packages="$packages python3"
      if [ "$need_compose" -eq 1 ]; then
        compose_package=
        for name in docker-compose-v2 docker-compose-plugin; do if apt-cache show "$name" >/dev/null 2>&1; then compose_package=$name; break; fi; done
        [ -n "$compose_package" ] || fail 'в APT не найден Docker Compose v2'
        packages="$packages $compose_package"
      fi
      # Список состоит только из фиксированных имён пакетов, не из ввода пользователя.
      DEBIAN_FRONTEND=noninteractive apt-get install -y $packages ;;
    dnf|yum)
      [ "$need_docker" -eq 0 ] || packages="$packages docker"
      [ "$need_compose" -eq 0 ] || packages="$packages docker-compose-plugin"
      [ "$need_iproute" -eq 0 ] || packages="$packages iproute"
      [ "$need_nft" -eq 0 ] || packages="$packages nftables"
      [ "$need_python" -eq 0 ] || packages="$packages python3"
      "$manager" install -y $packages ;;
    zypper)
      [ "$need_docker" -eq 0 ] || packages="$packages docker"
      [ "$need_compose" -eq 0 ] || packages="$packages docker-compose"
      [ "$need_iproute" -eq 0 ] || packages="$packages iproute2"
      [ "$need_nft" -eq 0 ] || packages="$packages nftables"
      [ "$need_python" -eq 0 ] || packages="$packages python3"
      zypper --non-interactive install $packages ;;
    pacman)
      [ "$need_docker" -eq 0 ] || packages="$packages docker"
      [ "$need_compose" -eq 0 ] || packages="$packages docker-compose"
      [ "$need_iproute" -eq 0 ] || packages="$packages iproute2"
      [ "$need_nft" -eq 0 ] || packages="$packages nftables"
      [ "$need_python" -eq 0 ] || packages="$packages python"
      pacman --noconfirm -Sy --needed $packages ;;
    apk)
      [ "$need_docker" -eq 0 ] || packages="$packages docker"
      [ "$need_compose" -eq 0 ] || packages="$packages docker-cli-compose"
      [ "$need_iproute" -eq 0 ] || packages="$packages iproute2"
      [ "$need_nft" -eq 0 ] || packages="$packages nftables"
      [ "$need_python" -eq 0 ] || packages="$packages python3"
      apk add --no-cache $packages ;;
  esac
fi
if ! docker info >/dev/null 2>&1; then
  if has systemctl; then systemctl enable --now docker
  elif has rc-service; then rc-update add docker default; rc-service docker start
  elif has service; then service docker start
  else fail 'Docker daemon не работает; запустите его и повторите установку'; fi
fi
docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 недоступен'
if [ ! -c /dev/net/tun ] && has modprobe; then modprobe tun || true; fi
[ -c /dev/net/tun ] || fail '/dev/net/tun недоступен'

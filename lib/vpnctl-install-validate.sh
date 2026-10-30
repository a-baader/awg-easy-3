#!/bin/sh
# Проверка до установки Python и остальных зависимостей.
set -eu
fail() { printf 'Ошибка параметров: %s\n' "$*" >&2; exit 64; }
port() { case "$1" in ''|*[!0-9]*) fail 'порт должен быть от 1 до 65535' ;; esac; [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || fail 'порт должен быть от 1 до 65535'; }
language() { case "$1" in en|ru|fa|es|zh-cn) ;; *) fail 'язык: en, ru, fa, es или zh-cn' ;; esac; }
awg_port=${AWG_PORT-};wg_port=${WG_PORT-}
awg_panel=${AWG_PANEL_PORT:-51821};wg_panel=${WG_PANEL_PORT-};lang=${AWG_LANG:-en}
awg_port_set=${AWG_PORT+x};wg_port_set=${WG_PORT+x}
remove=0;reinstall=0
for argument in "$@"; do
  case "$argument" in -h|--help|-help)
    printf 'Использование: sudo vpnctl install [--host HOST] [--port UDP] [--panel-port TCP] [--lang LANG]\n'
    cat <<'HELP'
Команды:
  help            Показать справку
  install         Установить или дополнить сервисы AWG и WireGuard
  reinstall       Выполнить чистую переустановку с новыми ключами
  status          Показать состояние сервисов
  start           Запустить сервисы
  stop            Остановить сервисы
  restart         Перезапустить сервисы
  settings        Показать настройки сервисов
  logs            Показать журналы сервисов
  diagnose        Проверить состояние VPN и трафик клиента
  update          Обновить исходники и образы из последнего стабильного релиза
  backup          Создать резервную копию
  uninstall       Удалить сервисы и рабочие данные, сохранив внутренние backup
  purge           Удалить все сервисы, данные и внутренние backup; сохранить исходники
  restore         Восстановить проверенный архив
  reset-password  Сбросить пароль и отозвать сессии
  client          Управлять клиентами: list, add, disable, enable, remove, export
  recover         Восстановиться после прерванной операции
  auto-update     Управлять обновлением stable: enable, disable, status, run
  storage         Показать хранение backup/кешей и подтвердить очистку
Компоненты: all — оба сервиса, awg — AmneziaWG, wg — WireGuard.
По умолчанию — читаемый вывод и JSON-файл в /root; --json — JSON в stdout.
После установки Python подробная справка: vpnctl КОМАНДА --help.
HELP
    printf 'Доступны --uninstall и --reinstall. Удаление данных требует подтверждения, в автоматизации --yes.\n'
    exit 0 ;;
  esac
done
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port|--awg-port) [ "$#" -ge 2 ] || fail 'не указан UDP-порт'; awg_port=$2; awg_port_set=x; shift 2 ;;
    --wg-port) [ "$#" -ge 2 ] || fail 'не указан UDP-порт'; wg_port=$2; wg_port_set=x; shift 2 ;;
    --panel-port|--awg-panel-port) [ "$#" -ge 2 ] || fail 'не указан TCP-порт'; awg_panel=$2; shift 2 ;;
    --wg-panel-port) [ "$#" -ge 2 ] || fail 'не указан TCP-порт'; wg_panel=$2; shift 2 ;;
    --lang) [ "$#" -ge 2 ] || fail 'не указан язык'; lang=$2; shift 2 ;;
    --config|--host|--data-root|--awg-image|--wg-image|--awg-interface|--wg-interface|--awg-subnet|--wg-subnet)
      [ "$#" -ge 2 ] && [ -n "$2" ] || fail "нет значения $1"; shift 2 ;;
    --uninstall) remove=1; shift ;;
    --reinstall) reinstall=1; shift ;;
    install|reinstall|all|awg|wg|--yes|--dry-run|--json|--build|--config=*) shift ;;
    --*=*) fail 'используйте отдельные аргументы: --параметр значение' ;;
    *) fail "неизвестный параметр $1" ;;
  esac
done
[ "$remove$reinstall" != 11 ] || fail '--uninstall и --reinstall несовместимы'
[ -z "$awg_port_set" ] || port "$awg_port"
[ -z "$wg_port_set" ] || port "$wg_port"
port "$awg_panel"
[ -z "$wg_panel" ] || port "$wg_panel"
language "$lang"

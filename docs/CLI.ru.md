# Контракт vpnctl

Host CLI использует Python 3 standard library, Docker CLI и Compose v2.
Третьего постоянного управляющего контейнера нет. После установки команда
доступна как /usr/local/sbin/vpnctl из любого каталога. Исходники остаются в
каталоге установки; не перемещайте его. --config PATH передаётся перед командой.
Все операции, кроме help, требуют root. Без аргументов открывается меню.

```sh
vpnctl help
vpnctl install --build --host vpn.example.org --lang ru
vpnctl status all --json
vpnctl start all
vpnctl stop wg
vpnctl restart awg
vpnctl settings wg --json
vpnctl logs all --tail 100
vpnctl logs wg --follow
vpnctl diagnose all --json
vpnctl backup all --output /root/dual-backup.tar.gz --yes
vpnctl restore --file /root/dual-backup.tar.gz --component wg --yes
vpnctl update all --manifest /root/release.json --yes
vpnctl reset-password --component wg
vpnctl client list --component all --json
vpnctl client add --component wg --name Телефон --group home
vpnctl client disable --component wg --name Телефон
vpnctl client enable --component wg --name Телефон
vpnctl client remove --component wg --name Телефон --yes
vpnctl client export --component wg --name admin --format conf --output /root/admin.conf
vpnctl client export --component wg --name admin --format qr --output /root/admin.svg
vpnctl client export --component awg --name admin --format vpn-url --output /root/admin.vpn-url
vpnctl uninstall wg --yes
vpnctl recover --yes
```

Компонент по умолчанию all у lifecycle/status/settings/logs/diagnose/backup/update/
uninstall. Restore требует --component; пароль и изменение/экспорт клиента
требуют одного конкретного backend. Для WG формат vpn-url возвращает ошибку.
Существующий файл экспорта не перезаписывается. Вывод профиля в stdout
разрешён только явно вызванной командой export без --output; журналы его не содержат.
QR экспортируется как SVG с содержимым native .conf, включая приватный ключ.

Reset создаёт новый случайный пароль и показывает его один раз. Собственный
пароль принимается через --password-stdin, без передачи в argv; компонент
должен работать. API и CLI используют один ClientManager и одну очередь записи.
Последнего активного Home-клиента отключать или удалять нельзя.

--yes заменяет интерактивное подтверждение потенциальной остановки/замены данных.
Без терминала подтверждение обязательно. Install/update/restore/uninstall
поддерживают --dry-run. Команды изменений сериализуются общей блокировкой,
совместимой с legacy updater. При transaction.json сначала нужен recover.
Он откатывает незавершённую транзакцию или завершает уже зафиксированную.
Неизвестная или живая legacy-блокировка автоматически не удаляется.

## Пакетная диагностика

```sh
vpnctl diagnose wg --probe-profile /root/dedicated-test.conf \
  --peer-role home --internet-url https://example.com/ --dns --json
```

Профиль должен принадлежать отдельному неиспользуемому тестовому peer и иметь
права 0600. Использование активного пользовательского профиля вызовет roaming
endpoint и прервёт его трафик. Одноразовый контейнер без host networking
проверяет собственную панель, HTTP через VPN, DNS (при --dns), запрет чужой
панели и доступа к публичным TCP-портам панелей. Профиль передаётся через stdin,
не аргументы и не Docker logs. Для Guest укажите --peer-role guest.

Без --probe-profile выводится состояние engine/панели/peers/маршрутов/firewall,
а пакетная проверка помечается requires_test_peer. Положительный handshake
сам по себе не считается подтверждением передачи пакетов. Отрицательные
проверки не заменяют полную матрицу Home/Guest/GeoIP/discovery и внешний WAN-тест.

## Ошибки и совместимость

Коды: 0 успех; 1 ошибка операции приложения/внешней команды; 64 параметры или
отказ подтверждения; 65 данные/схема/manifest; 69 среда/зависимости/root;
73 конфликт или блокировка; 74 ввод/вывод/протокол; 75 незавершённая транзакция;
130 прерывание. Preflight может завершиться 73. Ошибки не печатают raw stderr
с конфигурацией. Журналы очищаются от ключей, bcrypt, паролей, сессий и vpn://.

Совместимый awg-easy-3 делегирует AWG-команды установленному Dual manager.
Legacy auto-update заменён сообщением о ручном общем update; второй независимый
updater не создаётся. Отдельного публичного релиза Dual пока нет.

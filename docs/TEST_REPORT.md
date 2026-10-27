# Протокол испытаний VPN Easy Dual

Рабочая ветка feature/vpn-easy-dual, база v0.1.6
930dc6021024db9c8bbda7d92944aa9fef7baf3c. Проект находится в разработке;
полная Definition of Done ещё не выполнена. Все сетевые запуски выполняются
в одноразовых Docker namespaces/volumes, без host network и изменения
firewall рабочего VPS. Публикация образов и релизов не выполнялась.

## Выполнено

| Этап | Проверка | Фактический результат |
| --- | --- | --- |
| 0 | Исходный набор без изменений | 238/238 на Linux host Node 18.19.1 |
| 1 | Изоляция ownership и старые регрессии | 242/242 |
| 2 | Backend, native export, стандартные tools, AWG регрессии | 246/246 |
| 2 | Dockerfile target wg и awg | Обе сборки успешны; бинарники smoke проверены |
| 2 | scripts/test-wg-backend.cjs | Стандартный conf импортирован; TUN/userspace, handshake, HTTP через туннель, healthcheck и graceful shutdown прошли |
| 3 | Существующий набор после UI/capabilities | 246/246; дополнительные QR/HTTP проверки — 12/12 |
| 4 | Регрессионный набор | 251/251; дополнительная проверка порядка firewall/engine прошла отдельно |
| 4 | Python preflight, Docker Compose config | Проверки конфликтов прошли, Compose v2.39.2 config корректен |
| 4 | Dual shared namespace | AWG/WG Home/Guest IPv4/IPv6 NAT, Home-to-Home, меж-VPN/Guest/WAN запреты, discovery, независимые restart и штатное переподключение прошли |

Логи и исходный тестовый образ сохранены в
`/app/srv_awg/dual-work/evidence/`. Дополнительные новые тесты проверяют
очистку QR Blob URL, игнорирование запоздалого ответа после закрытия окна,
отказ от отображения невалидного SVG, WireGuard capabilities/manifest и
запрет экспорта без сессии. I18n тесты проверяют все пять языков.

## Пока не проверено

Полная T01–T26 матрица (включая дополнительные hairpin/Docker/отказные сценарии),
аварийное восстановление и reboot остаются незавершёнными. Проверки host CLI,
backup/restore/rollback и миграции описаны ниже по этапам.
Windows 10/Android/iOS и визуальная проверка браузером не выполнялись.
Успешный handshake не подменяет проверку фактической передачи пакетов;
на этапе 2 проверена передача HTTP к собственной WG-панели. Реальный Internet
и меж-VPN изоляция этим тестом ещё не подтверждены.

## Этап 5: host manager и требования PRODUCT_SPEC

Проверено в текущей рабочей ветке:

| Проверка | Результат |
| --- | --- |
| Полный Node-набор, включая очередь auth/client и maintenance | 256/256 |
| Python host manager: порты, snapshot, partial restore, rollback, idempotence, архивы, блокировки и crash phases | 11/11 |
| Python preflight | 2 теста, включая группы конфликтов интерфейсов/маршрутов/портов/Docker |
| Повторная сборка target awg/wg | Успешно, локальные теги dual-stage5 |
| Реальный manager в общем disposable namespace | Fresh install, два healthcheck, повторный install, CRUD обоих backend, .conf/QR, reset-password, partial WG restore, последовательный update и искусственный отказ с rollback прошли |
| Реальная пакетная диагностика обоих backend | Собственная панель и HTTP NAT доставлены, DNS получил ответ через VPN, чужая панель запрещена |
| NixOS bootstrap в readonly disposable container | Вывод отдельного модуля, imports, rebuild max-jobs=1/cores=1; без пакетных/системных изменений |
| POSIX shell / Python compile / npm check / diff check | Пройдены |

Команды воспроизведения после сборки обоих образов с тегами dual-stage5:

```sh
python3 scripts/test-vpnctl.py
python3 scripts/test-dual-preflight.py
python3 scripts/test-dependency-bootstrap.py
sudo -E python3 scripts/test-vpnctl-integration.py
sudo -E python3 scripts/test-vpnctl-packet-probes.py
```

TEST_AWG_IMAGE/TEST_WG_IMAGE позволяют задать свои локальные теги. Нужны Docker
Compose v2, Docker API, /dev/net/tun и Linux/amd64. Интеграционные сценарии
перенаправляют network/sysctl/nft/ip в одноразовый anchor namespace, а системные
файлы manager — во временный system_root. Моделирование host network этим
способом не заменяет reboot отдельной VM. Пакетный тест использует локальный
WAN-стенд с публичным тестовым адресом, HTTP и DNS; внешний Internet не проверен.

Обнаруженные и исправленные ошибки: отсутствующий docker run -i в bootstrap;
устаревшие CLI mocks после SafeLog; риск потери UI-записей при rollback после
snapshot. Отдельно исправлен тестовый DNS responder: UDP-ответ должен исходить
с адреса запроса, чтобы проходить обратный conntrack/NAT.

Остаются этапы 6–7: миграция настоящего оригинального 0.1.6 с существующими
профилями, reboot/аварийное прерывание в VM, полная расширенная T01–T26 матрица,
CI обоих образов и итоговая документация. Браузерная проверка отложена по
пожеланию пользователя. Проект пока не объявляется готовым к приёмке.

## Этап 6: миграция оригинального 0.1.6

`scripts/test-vpnctl-migration.py` успешно выполнен после добавления
AWG_PANEL_PORT=51821 в environment тестового Compose. Эта переменная требуется
старому healthcheck оригинала; раньше панель работала, но healthcheck сообщал
`wget: bad port ''` и останавливал испытание до миграции.

Проверен оригинальный образ 0.1.6 по digest
`sha256:4a218ff42b401fd84a25c576090a42d52e204d8f3b4fd530ec352021580199ec`.
Клиент и профиль созданы оригинальным кодом. Подтверждены HTTP до миграции,
побайтовое сохранение state.json и клиентского .conf, сохранение UDP-порта
54321, проверенный snapshot v1 и передача HTTP прежним работающим клиентом
после миграции без его restart, изменения конфигурации или перевыпуска.
Отдельная WG-панель и новый Home admin также проверены.

```sh
sudo -E python3 scripts/test-vpnctl-migration.py
```

Испытание использует одноразовый общий network namespace и временный
system_root. Проверяется вызов отключения legacy updater; systemd/OpenRC
хоста не вызываются. Все тестовые контейнеры и данные удаляются в finally.
Лог: `/app/srv_awg/dual-work/evidence/stage6-migration-port-fixed.log`.
Аварийное прерывание миграции и reboot отдельной VM этим тестом не проверены.

## Этап 6: прерывание и восстановление

Дополнительно проверены свежие сборки target awg/wg с тегами dual-stage6.
Полный Node-набор после добавления fsync каталога состояния: **256/256**.
Проверки Python manager: **17/17**, preflight: **2/2**.

Реальный процесс manager убит SIGKILL в четырёх точках:

| Точка прерывания | Подтверждённый результат recovery |
| --- | --- |
| После остановки WG до snapshot | Снята блокировка погибшего процесса, возвращены исходные состояние/образ и доступ к панели |
| После обновления AWG, до обновления WG | Обоим компонентам возвращены соответствующие прежние образы и snapshot |
| После committed, затем остановка обоих контейнеров | Запущены новые образы; сохранена запись клиента, принятая после commit; отката старого snapshot нет |
| После замены каталога при restore WG | Возвращено состояние до restore, включая принятую ранее запись; обе панели работают |

После каждого recovery проверены image ID, байты state.json, HTTP панели и
возможность добавить/удалить клиента. Блокировки и transaction.json сняты после
успеха. Отдельный миграционный тест теперь убивает manager после остановки
оригинального 0.1.6 до snapshot, подтверждает восстановление исходного
контейнера/клиента, затем выполняет полную успешную миграцию.

```sh
TEST_AWG_IMAGE=vpn-easy-awg:dual-stage6 TEST_WG_IMAGE=vpn-easy-wg:dual-stage6 \
  sudo -E python3 scripts/test-vpnctl-recovery.py
TEST_AWG_IMAGE=vpn-easy-awg:dual-stage6 TEST_WG_IMAGE=vpn-easy-wg:dual-stage6 \
  sudo -E python3 scripts/test-vpnctl-migration.py
```

Дополнительные проверки защищают от замены чужого контейнера при recover,
повреждённого snapshot, потери файлов при частичной регистрации manager и
перезаписи системного файла, изменённого администратором. Исправлена гонка
удаления временного Docker-контейнера между ps/inspect: пропуск разрешён
только после подтверждения его отсутствия.

Логи: stage6-sigkill-recovery-complete.log,
stage6-migration-recovery-final.log, stage6-node-durability.log,
stage6-recovery-unit-final.log в /app/srv_awg/dual-work/evidence/.
SIGKILL и остановка контейнеров не заменяют отключение питания или reboot VM:
T23 и reboot в середине транзакции остаются непроверенными.

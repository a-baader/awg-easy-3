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

## Этап 7: расширенная матрица и CI

Оба target повторно собраны с локальными тегами dual-stage7. Полный набор
Node: **256/256**, Python manager: **23/23**, preflight: **2/2**.
Фактическая версия package.json обоих образов сверяется с release manifest;
несовпадение отвергается до snapshot и изменений состояния. YAML workflows,
Compose config, POSIX shell и Python/Node syntax проверены локально.

В сетевых тестах обнаружен и исправлен обход собственной панели через DNAT
публичного порта. Для Dual добавлен общий запрет ct status dnat от VPN-клиента
до разрешений Home. Положительные контроли используют работающий host listener
и существующую WG-панель; проверены IPv4 и IPv6. Отдельный Docker-адрес
принудительно направляется через клиентский туннель с fwmark для внешнего UDP.

| Сценарий ТЗ | Результат в disposable окружении |
| --- | --- |
| T01–T04: AWG/WG Home/Guest → Internet | IPv4/IPv6 HTTP через NAT к WAN-стенду; DNS отдельно подтверждён пакетным probe |
| T05–T06: Home A ↔ Home B | HTTP в обоих направлениях, IPv4/IPv6 |
| T07–T08: Guest → Home/своя панель | Запрещено |
| T09–T12: чужая панель и VPN subnet | Запрещено для IPv4/IPv6 |
| T13: WAN → панели | Запрещено при наличии маршрута к действующему listener |
| T14: IPv4/IPv6 permissions | Включение/отзыв семейства проверены на работающих peer без замены профиля |
| T15: отключение peer | Перед отзывом установлен keep-alive TCP-поток; после отзыва счётчик доставленных данных перестал расти |
| T16: GeoIP | Synthetic allow/block, пустой allow set и потеря базы проверены для обоих backend |
| T17: mDNS/SSDP | Home fan-out; нет доставки Guest/WAN/чужому VPN; Guest-origin не ретранслируется |
| T18–T19: независимый restart | Другая VPN продолжает передавать HTTP к панели и WAN; прежний клиент переподключается без изменения профиля |
| T20: backup/restore | Реальный partial restore и rollback после прерывания restore прошли |
| T21: отказ update | Проверены healthcheck failure, SIGKILL и сохранение подтверждённой записи после commit |
| T22: исходный AWG 0.1.6 → Dual | Старый клиент и профиль сохранены, HTTP до/после миграции; прерывание до snapshot восстановлено |
| T23: reboot хоста | **Отложено пользователем, не проверено** |
| T24: повторный install/update | Данные/ключи сохраняются, повторный install не переинициализирует; update/recovery проверены повторными транзакциями |
| T25–T26: uninstall одного VPN | Реальный peer оставшегося VPN передаёт HTTP к панели и через WAN NAT; image не изменён, чужая nft table сохранена |

GeoIP использует synthetic labels/prefixes, а не фактическую геолокацию
тестовых адресов. При утрате базы и сохранённых ограничениях исходная логика
отказывает в старте соответствующего VPN (fail-closed); второй VPN продолжает
работать. После возврата базы экземпляр запускается с прежним профилем.
Это сохранение исходного поведения, а не новый режим разрешения без базы.

Дополнительно прошёл исходный scripts/test-awg-upgrade.cjs с образом
предыдущего выпуска 0.1.5: клиенты не перезапускались, проверены сохранение
всех exports/состояния, 2 MiB HTTP IPv4/IPv6, ограничения семейств, upgrade и
rollback сервера. Лог stage7-original-upgrade-regression.log.

CI конфигурирует сборку обоих образов и эти тесты в Ubuntu runner. Публикация
по push/tag отключена; отдельный workflow_dispatch требует явного publish=true.
GitHub Actions **не запускались** в текущем локальном испытании; проверены
синтаксис и соответствующие локальные команды. Образы/релиз не публиковались.

Основные логи в /app/srv_awg/dual-work/evidence/:

- stage7-network-matrix.log — расширенные сетевые сценарии;
- stage7-uninstall-packets.log — T25/T26 с реальным peer;
- stage7-uninstall-integration.log — CRUD/update/restore/uninstall;
- stage7-node-final.log, stage7-manager-final.log — unit/regression;
- stage7-release-image-version.log — версии настоящих образов.

Для воспроизведения T25/T26:

```sh
TEST_AWG_IMAGE=vpn-easy-awg:dual-stage7 TEST_WG_IMAGE=vpn-easy-wg:dual-stage7 \
  sudo -E python3 scripts/test-vpnctl-uninstall.py
```

## Границы текущей приёмки

По прямому указанию пользователя reboot-тесты отложены: не выполнены T23 и
перезагрузка VM в середине update/restore. QEMU и /dev/kvm в среде отсутствуют;
рабочий хост/VPS не перезагружался. SIGKILL и restart контейнеров не считаются
заменой reboot/power-loss испытания. Браузерная проверка также отложена.

Не проверены официальные приложения Windows/Android/iOS, реальный внешний
Internet и автоматическая установка зависимостей на всех заявленных
дистрибутивах. NixOS проверен как генерация декларативной инструкции, без
nixos-rebuild. При отдельном host firewall с INPUT DROP необходимо разрешение
входа со своего VPN-интерфейса, описанное в NETWORK_SECURITY.ru.md.

Автоматизированные локальные проверки завершены в указанных пределах.
**Полная Definition of Done, включающая reboot и полевые проверки, не объявляется
выполненной.** Публикация и применение к рабочему VPS не выполнялись.


## Дополнительные проверки финальной ревизии

Повторное восстановление после rollback теперь имеет собственную устойчивую
фазу rollback_committed. Реальный SIGKILL после неё с последующей принятой
записью клиента подтвердил, что повторный recover не заменяет эту запись
старым snapshot. При неопределённом результате записи commit на диск
автоматический rollback/thaw запрещён: сохраняется maintenance, требуется
исправление хранения и recover по сохранённому журналу.

Миграционный тест также искусственно отказывает после успешного запуска нового
WG: исходные image/state AWG и HTTP прежнего клиента восстановлены; следующая
миграция проходит. Логи: stage7-recovery-rollback-window.log и
stage7-migration-rollback-final.log.

Владение контейнером проверяется и перед каждым control RPC, включая изменение
клиента и сброс пароля: image ID, mount, host networking и Compose service.
Несовпадение блокируется до docker exec. Изменение через чужой mount проверено
unit-тестом; T25/T26 повторены с реальными control RPC и peer-пакетами
(stage7-uninstall-owned-control.log).

Дополнительно отдельно проверены Home A, Home B и Guest обоих протоколов против
чужих IPv4/IPv6 панелей и клиентов, Guest к своей IPv6 панели/Home и WAN→DNAT.
Лог stage7-all-peer-isolation.log. Сборка обоих target через метод vpnctl --build
прошла на Docker без buildx с выбором доступного legacy builder
(stage7-manager-build.log). Миграция экспериментальных версий (например 0.3.0)
отвергается до изменения данных; повторный install настроенного Dual не
запускает legacy detection. Оригинальный образ по digest отдельно подтвердил
package version 0.1.6 (stage7-original-version.log).

## Предрелизная проверка 0.2.0-rc.1

По последнему указанию пользователя **до публикации обязательны браузер и
GitHub Actions**; полевые устройства и клиенты проверяются после релиза.
Reboot-тесты остаются отложенными.

Локальная браузерная проверка выполнена Chromium 156 через Playwright 1.64.1
на Linux, в 1440×1050 и 390×844, с DPR 2. Браузер обращался к настоящим
панелям через ограниченный HTTP proxy в Home peer; counters AWG/WG подтвердили
шифрованный трафик. Проверены вход/выход, создание/удаление клиента, группы и
IPv4, пять языков и FA RTL, отсутствие горизонтального overflow, смена пароля
и отзыв второй браузерной сессии. Native QR декодирован jsQR и побайтово
сравнён со скачанным .conf; AWG QR vpn:// сохранён, WG-несовместимые кнопки
скрыты. JavaScript/CSP ошибок не выявлено. Desktop/mobile screenshots
просмотрены. Screenshots не содержат QR, паролей или приватных ключей.

Логи browser-real-panels-labels.log и report/screenshots в evidence/browser
сохранены локально. Браузерный сценарий добавлен в job Dual GitHub Actions.
Первый GitHub Actions run 40467861134 прошёл Node и исходный container job,
но остановился в migration test из-за shallow checkout без baseline commit.
Для Dual установлен fetch-depth: 0. Перед публикацией workflow требует
успешный Test workflow именно для SHA публикуемого тега.

Версия приложения и два образа переведены на 0.2.0-rc.1; legacy Compose
продолжает закреплять оригинальный 0.1.6 для совместимости. Release manifest
формируется по фактическим digest обоих образов, снабжается SHA256SUMS и
командой установки. Статус итогового CI и ссылки на публикацию фиксируются
в артефактах релиза после фактического успешного запуска.

## Изменения установки после v0.2.0-rc.1 — 2026-10-27

Установщик перенесён в `vpnctl`; корневой `install.sh` делегирует ему.
Оригинальный установщик 0.1.6 сохранён побайтово как тестовый эталон в
scripts/fixtures; его Git blob совпадает с baseline. Его прежние проверки
сохраняются отдельно от проверок действующего Dual-установщика.

Подтверждено локально:

- 256/256 Node tests; `npm run check` без ошибок.
- 23/23 прежних unit-теста manager и 19/19 новых проверок контракта установки.
- 2/2 группы preflight; Python compile, `sh -n`, `git diff --check`.
- Новые проверки: исходные флаги и AWG_* переменные, приоритет CLI, проверка
  до зависимостей, cwd/symlink, подтверждение/отмена/EOF, все UDP sockets,
  повторный выбор порта, удаление только своих неизменённых системных файлов,
  сохранение внутреннего backup, структура systemd/OpenRC и вызовы служб,
  проверка stable manifest и пропуск RC.
- Реальные контейнеры с опубликованными digest RC1 в отдельном namespace:
  неудачная переустановка WG вернула прежние state/bootstrap и рабочий старый
  профиль; успешная создала новые ключи и единственного admin, сохранив AWG;
  оба VPN передали HTTP к собственной панели.
- Полное удаление убрало оба контейнера, рабочие данные, bootstrap и установленный
  vpnctl. Чужой контейнер, nftables sentinel и текущие forwarding сохранились.
  Оба VPN восстановлены из оставшегося внутреннего backup; передача HTTP
  подтверждена снова для AWG и WG.

Первый дополнительный restore после удаления выявил выбор каталога исходников
вместо архивного пути данных. Выбор исправлен; повторный полный сценарий прошёл.
Логи сохранены в `/app/srv_awg/dual-work/evidence/install-contract/`.
Новые проверки добавлены в GitHub Actions. Сам запуск CI фиксируется отдельно
после отправки commit; локальный успех не считается результатом GitHub Actions.

Полный reboot VM, reboot во время update/restore и физические/полевые клиенты
остаются отложенными по решению пользователя. Проверка unit-файлов и mock
вызовов init не считается подтверждением фактического запуска после reboot.

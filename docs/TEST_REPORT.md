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

Полная T01–T26 матрица (включая дополнительные hairpin/Docker/отказные сценарии), миграция работающего
AWG, host CLI, backup/restore/recovery и reboot относятся к следующим этапам.
Windows 10/Android/iOS и визуальная проверка браузером не выполнялись.
Успешный handshake не подменяет проверку фактической передачи пакетов;
на этапе 2 проверена передача HTTP к собственной WG-панели. Реальный Internet
и меж-VPN изоляция этим тестом ещё не подтверждены.

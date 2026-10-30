# Текущий протокол проверок после ревью

Эта страница описывает невыпущенные изменения P1/P2/P3 рабочей ветки.
Стабильный опубликованный выпуск определяется GitHub Releases и веткой master;
рабочая release-ветка может содержать VERSION следующего выпуска.
Исторический протокол [0.2.3](archive/review-baseline/TEST_REPORT.v0.2.3.md)
не подменяет проверки текущего кода.

## Обязательные проверки

| Сценарий | Что должно быть подтверждено |
| --- | --- |
| Unit Node/Python и preflight | API, auth/revocation, схемы, экспорт, journal, ownership, CLI, прогресс, архивы, retention |
| Синтаксис, actionlint и ссылки | Python/JavaScript/shell/YAML, актуальные версии документации, существующие локальные ссылки |
| Production AWG/WG | Сборка настоящих target, отсутствие `/app/test`, совместимые tools/engine |
| Панели | Home своего VPN открывает свою панель; Guest, другой VPN, WAN, host/Docker-обход и посторонний DNAT закрыты |
| Discovery | Настоящий multicast `224.0.0.251:5353` и `239.255.255.250:1900`, а также unicast к собственному VPN-адресу; fan-out только своему Home |
| Runtime engine | SIGKILL настоящего AWG/WG после ready, restart своего контейнера, прежний профиль и HTTP; соседний VPN не перезапускается |
| Lifecycle | Install/extend, backup/restore, update/rollback, recover после SIGKILL, uninstall/reinstall/purge, source update/no-op |
| Защита данных | Нет оставшихся учтённых `.failed-*`/`.before-restore-*`; backup сохраняется до purge; чужие ресурсы и forwarding сохранены |
| Browser | Chromium через настоящие Home AWG/WG: вход, CRUD, conf/QR, мобильный вид, языки, пароль и сессии |
| Release pipeline | Успешный полный CI точного commit; продвижение проверенных image ID без пересборки, provenance, готовые assets до продвижения master |

Проверки выполняются только в disposable Docker/network namespace и временных
system root. Handshake, наличие интерфейса или локальный HTTP не считаются
доказательством доставки peer-трафика. Пакетные тесты отправляют настоящий HTTP
и DNS через туннель; discovery отдельно проверяет multicast и unicast.

При отказе readiness сохраняются очищенные healthcheck/process logs до удаления
контейнеров. Принимается только настоящий `healthy` в пределах срока ожидания;
постоянный `unhealthy` не игнорируется.

## Результаты и пределы

После исправлений P2 прошли 267 Node-тестов, 163 Python/preflight, полный
lifecycle, 24 быстрых recreate при смене maintenance, сетевые и browser-проверки,
миграция, purge и SIGKILL/recover. После P3 прошли 268 Node-тестов, 166 Python/preflight, все перечисленные
контейнерные и browser-сценарии. Оба production target собраны настоящим
Dockerfile; тестовых файлов в них нет. Проверены 248 локальных Markdown-ссылок,
версии документации, actionlint и default/legacy Compose без запуска сервисов.
Новый релиз этим не объявляется опубликованным.

По решению пользователя R09 ограничен capabilities/resource limits. Web/API
остаётся root с NET_ADMIN; полное разделение с helper отложено.
Оставшиеся P1 R03/R04 в эти этапы не включены.

## Последующие исправления R03/R04

Node.js обновлён до 22.23.4, Go до 1.27.3, Alpine до 3.23.7; digest закреплены.
Security-lock файлы Go-зависимостей сохранены в scripts/engine-dependencies,
commit исходников движков сохранены. Runtime не содержит package managers,
git и тестовых файлов. Полный suite проверялся в отдельном Debian с Git и
frozen pnpm dependencies; исходные timeout Git-сценариев не увеличивались.

После R04 прошли 279 Node tests, включая отдельные worker threads для bcrypt,
границы rate/concurrency, HTTP 429/Retry-After, запрет обхода через
X-Forwarded-For и гонку со сменой passwordHash/sessionSecret. Проверка настоящего
bcrypt в обоих production-образах при 2 CPU, 1 GiB и 256 PID подтвердила
доступность HTTP/очереди управления и отказ лишним параллельным login.
Browser через настоящие Home AWG/WG: вход, CRUD, conf/QR, языки, mobile,
смена пароля, отзыв сессии и logout — PASS.
Перед подготовкой кандидата повторно прошли 166 Python/preflight проверок;
проверены 251 локальных Markdown-ссылок. Fresh restore в тестах использует
manifest образов этого CI, чтобы проверять кандидат до его публикации;
production-проверка доверенных образов не отключается.

Trivy: HIGH/CRITICAL = 0 для обоих итоговых образов, без ignore/VEX исключений.
Три UNKNOWN относятся к OpenPGP и x/net/html; официальные записи Go и
go list -deps подтверждают отсутствие затронутых пакетов в сборках движков.
Полные scanner-отчёты сохраняются. Сканирование включено в CI кандидата
до сохранения tested-images и отдельно выполняется еженедельно для stable.

После обновления Go/Alpine прошли upstream padding/disabled-cookie regressions
(count=5), upgrade/rollback со старыми AWG-клиентами и полный dual-network,
включая приоритетную изоляцию панелей Home своей VPN-подсетью.
GitHub Actions точного итогового commit проверяются отдельно перед выпуском.

## Отложено по решению пользователя

- Reboot отдельной VM и фактический systemd/OpenRC после загрузки ОС.
- Отключение питания во время update/restore.
- Полевые Windows/Android/iOS и физические устройства, включая их multicast-маршруты.

Структурные проверки boot-служб и headless browser не подменяют эти сценарии.

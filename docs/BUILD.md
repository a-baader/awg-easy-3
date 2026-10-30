# Сборка wg-dual-easy

Linux/amd64, Docker Engine; публикация образов не требуется. Оба target
используют общую кодовую базу и digest-pinned base images. По умолчанию
Dockerfile собирает AWG; для WG необходимо явно указать target.

```bash
docker build --target awg -t vpn-easy-awg:dual-dev .
docker build --target wg -t vpn-easy-wg:dual-dev .
```

Движки и tools закреплены точными commit в Dockerfile; лицензии и источники
перечислены в THIRD_PARTY_NOTICES.md. Образы WG не содержат awg и ссылок на
AWG binaries. Официальный wireguard-go запускается foreground-процессом
приложения, с явной настройкой TUN через wg/iproute2. Автоматического выбора
kernel WireGuard нет. wg-quick используется для семантического strip .conf.

Проверка WG backend без изменения сети host:

```bash
docker run --rm --network none --entrypoint wg vpn-easy-wg:dual-dev --version
docker run --rm --network none --entrypoint wireguard-go vpn-easy-wg:dual-dev --version
node scripts/test-wg-backend.cjs vpn-easy-wg:dual-dev
```

Последняя команда требует /dev/net/tun и разрешения запуска Docker containers
с NET_ADMIN. Она создаёт одноразовую сеть, volume и два контейнера, импортирует
выданный .conf официальными wg-quick/wg, проверяет TUN/userspace, handshake,
HTTP-трафик через VPN, healthcheck и корректную остановку сервера. Данные
удаляются в finally. Это не проверка Windows или реального выхода в Internet.

Unit tests: Node >=18 по контракту приложения; рекомендуемый для CI Node
22.23.4 и pnpm 11.19.0 из package.json. Для shell fixtures нужен git.

```bash
cd src
corepack enable
corepack prepare pnpm@11.19.0 --activate
pnpm install --frozen-lockfile
pnpm test
pnpm run check
```

Секреты, data/, data-wg/, .env и backups не должны попадать в Git или build
context. Перед публикацией образов/релизов требуется отдельное разрешение.
Dual Compose: docker-compose.dual.yml. Обязательные сетевые, миграционные и
аварийные тесты и их ограничения перечислены в TEST_REPORT.md.
CI собирает оба target без публикации; публикация требует отдельного ручного
workflow_dispatch с явно включённым publish для выбранного release tag.

Production target `awg` (также target по умолчанию) и `wg` копируют только
runtime-библиотеки, web assets, entrypoint и package metadata. Каталога
`/app/test` в этих образах нет. Для тестовых helper предусмотрены `awg-test`
и `wg-test`; CI использует их только там, где нужны встроенные fixtures.
Publisher продвигает production-образы, прошедшие полный CI, без пересборки.
Порядок выпуска: [RELEASE_PROCESS](RELEASE_PROCESS.ru.md).

Node.js в production и CI закреплён на security-выпуске 22.23.4; digest
официального Alpine-образа указан в Dockerfile. Полный unit suite запускайте
в окружении с git и shell-инструментами, а не в минимальном production-образе.
Пакеты тестового окружения в публикуемые образы не добавляются.
Runtime собирается на Alpine с копированием только Node binary: npm, Corepack,
pnpm и Yarn в него не попадают. Go-движки собираются Go 1.27.3 с проверенными
lock-файлами из `scripts/engine-dependencies`; commit самих движков сохранены.

Проверка OS-пакетов, Node.js и Go-зависимостей обоих образов:

```bash
python3 scripts/scan-release-images.py vpn-easy-awg:dual-dev vpn-easy-wg:dual-dev
```

Скрипт проверяет SHA-256 закреплённого Trivy, сохраняет JSON в security-report
и возвращает ошибку при HIGH/CRITICAL. Workflow Security раз в неделю проверяет
точные digest последнего stable-релиза и сохраняет отчёты, включая неуспешные.

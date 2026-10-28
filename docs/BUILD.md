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
22.18.0 и pnpm 11.19.0 из package.json. Для shell fixtures нужен git.

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

# Исходный аудит VPN Easy Dual

Дата: 2026-10-27. Этап 0; реализация приложения ещё не начата.

## Проверенная база

- Форк: `https://github.com/a-baader/awg-easy-3`.
- Оригинал: `https://github.com/alexsemenovru/awg-easy-3`.
- Исходная ветка `master`, тег `v0.1.6`.
- SHA: `930dc6021024db9c8bbda7d92944aa9fef7baf3c`.
- Перед аудитом рабочая копия чистая; SHA master форка и оригинала совпадают.
- Рабочая ветка задания: `feature/vpn-easy-dual`.
- Предыдущая экспериментальная реализация WireGuard не используется.

## Карта кода и точек расширения

| Область | Фактические файлы | Изменение для Dual |
| --- | --- | --- |
| Компоновка приложения | `src/lib/Application.js`, `src/server.js`, `src/cli.js` | Выбор backend, индивидуальный runtime, локальный control socket |
| Bootstrap | `src/lib/BootstrapInstaller.js`, `NetworkDetector.js`, `Ipv6Plan.js` | Индивидуальные интерфейс, адреса, порты; отдельные ключи WG |
| Конфигурации и экспорт | `AwgArtifacts.js`, `Awg3Config.js`, `AwgConfigRenderer.js`, `AmneziaVpnLink.js`, `DnsPolicy.js`, `ClientRoutes.js` | Семантический адаптер WG без AWG-параметров; QR native conf |
| Ключи | `AwgKeyManager.js`, `ProcessRunner.js` | Выбор `awg`/`wg`; секреты передаются инструментам через stdin |
| Состояние | `StateStore.js` | Сохранение AWG v1/v2; отдельная распознаваемая схема WG |
| Клиенты | `ClientManager.js`, `ClientPolicy.js`, `ClientTraffic.js`, `AddressAllocator.js` | Общий сервис и очередь изменений; root CLI через него |
| API, сессии | `ApiService.js`, `HttpServer.js`, `PasswordManager.js`, `SessionManager.js` | Сохранить модель; отдавать capabilities; отдельные секреты |
| Применение сети | `RuntimeApplier.js`, `NftablesPolicy.js` | Параметризация ownership и бинарников; независимые таблицы и маркеры |
| GeoIP | `GeoIpPolicy.js`, `GeoIpSource.js`, `GeoIpUpdater.js`, `GeoIpWorker.js` | Сохранить общую логику и fail-closed; отдельные каталоги |
| Discovery | `DiscoveryRelay.js` | Отдельный экземпляр на своём адресе, Home-only fan-out |
| Диагностика | `ClientDiagnostics.js` | Выбор бинарника backend; handshake не равен доказательству трафика |
| Интерфейс | `src/www`, пять локализаций | Общая панель с capabilities, индивидуальные branding и экспорты |
| Host lifecycle | `install.sh`, `awg-easy-3`, `lib/auto-update.sh` | Единый `vpnctl`, lock, backup/restore, миграция updater |
| Сборка и CI | `Dockerfile`, оба Compose, `.github/workflows` | Два образа; публикация только по отдельному разрешению |

## Фактические данные и авторизация

`StateStore` хранит `/data/state.json`; пути задаёт `AWG_EASY_DATA_DIR`.
Runtime — `/run/awg-easy-3`, переопределяется `AWG_EASY_RUNTIME_DIR`.
Запись состояния атомарна через временный файл, fsync и rename; каталог 0700,
файл 0600. Схема v1, GeoIP включает sticky v2. Старое значение enabled при
сохранении ограничено для fail-closed при downgrade. Данные содержат приватные
ключи, bcrypt-хеш и секрет сессий; публиковать их запрещено.

Пароль: bcryptjs, cost 12. Сессия подписана HMAC; общий секрет состояния
меняется при смене пароля, отзывая все сессии. Cookie HttpOnly/SameSite Strict;
HTTP-сервер проверяет Origin при изменениях. Клиентские изменения сериализует
ClientManager; после применения runtime сохраняется состояние, при ошибке
предусмотрен откат. CLI не должен обходить этот механизм отдельной записью JSON.

Панель слушает внутренние IPv4/IPv6 адреса состояния. Исходный Compose использует
host network без ports. Nft table `inet awg_easy_3`; четыре собственных маркера
в `ip/ip6 filter DOCKER-USER`. Клиентские IPv4/IPv6 разрешения проверяются в
INPUT/FORWARD/OUTPUT до установленных соединений. GeoIP хранит снимки в
`/data/geoip`; при активном фильтре без пригодной базы интернет закрывается.

## Зависимости и обнаруженные расхождения

- Node image: `22.18.0-alpine3.22`, digest `dbb65b3b08bd9d4d4a85299ad4d668b0e709a0601cecb5969f4dbb1dd89408aa`.
- Go builder: `1.25.12-alpine3.23`, digest `f128118f1f3a7f38949c57f03d29d13e4afdb06b86f840f8f43e8031e6c1a73a`.
- Alpine tools builder: `3.23`, digest `1beb0dc0a51de7ff38e3b5274078a2e0b81113ba5c7535e1a03d5913a5edbda3`.
- Dockerfile AWG Go: `b5928efb6ca19f0153958460c3d141f04abc5c2e`.
- AWG tools: `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843`.
- `docker-compose.build.yml` и THIRD_PARTY_NOTICES указывают устаревший Go
  `1b86b2ae0e493e7ea93f8c1a0f0cb6735b1551f1`; dev image помечен 0.1.0.
  Эти расхождения требуется исправить при подготовке сборки.
- pnpm `11.19.0`; production зависимости bcryptjs `2.4.3`, qrcode `1.5.4`.
  Версии, integrity и транзитивные зависимости записаны в `src/pnpm-lock.yaml`.
- В исходном образе `wg`/`wg-quick` — ссылки на AWG. Для WG это недопустимо:
  требуется отдельный target с официальными WireGuard binaries.
- Healthcheck жёстко привязан к awg0 и 10.8.0.1; для WG его нужно заменить
  проверкой собственного состояния, инструмента и панели.
- Updater по умолчанию выполняет автоматическое обновление после boot;
  для Dual требуется единый ручной последовательный lifecycle.
- `.gitignore` не содержит общего исключения `.env`, `/data` и backup.
  Их необходимо добавить перед реализацией менеджера.

## Лицензии

LICENSE содержит CC BY-NC-SA 4.0; NOTICE сохраняет атрибуцию Emile Nijssen,
wg-easy contributors и JohnnyVBut/awg-easy, независимый статус проекта.
Эти файлы сохраняются. Производные изменения должны быть обозначены.
AmneziaWG Go — MIT; AWG tools — GPL-2.0; bcryptjs/qrcode — MIT.
THIRD_PARTY_NOTICES перечисляет MIT/ISC транзитивные JS-зависимости.
Для официальных WireGuard Go/tools требуется добавить точные ревизии,
исходники и их лицензии при введении WG target. Юридическая совместимость
не подменяет обязательство включить notices и доступные исходники binaries.

## Baseline и ограничения окружения

Код не менялся перед исходными прогонами. Окружение: Linux/amd64,
Docker 29.1.3; host Node 18.19.1. Compose plugin и pnpm на host отсутствуют.
Образ исходного релиза скачан по digest
`sha256:4a218ff42b401fd84a25c576090a42d52e204d8f3b4fd530ec352021580199ec`.

1. Node 22.18.0 в исходном production-образе, код смонтирован read-only:
   **230/238**. Восемь updater-тестов требуют отсутствующий в образе git.
2. Отдельный тестовый образ с git, тот же исходный код и Node:
   **236/238**. `failed candidate health check restores the previous commit`
   и `exit during replacement preserves installed files and state`
   завершились `spawnSync sh ETIMEDOUT` при лимите 20 секунд.
   Проверки с локальными git fixtures занимают около 10–15 секунд;
   это ещё не доказательство дефекта runtime rollback.
3. Подготовка запуска на host остановилась при распаковке зависимостей:
   файловая система workspace запрещает hard links. Дополнительные тесты
   в этом окружении не были запущены.

Полные логи, список связей AWG и вспомогательный Dockerfile сохранены вне Git:
`/app/srv_awg/dual-work/evidence/`, `/app/srv_awg/dual-work/Dockerfile.audit`.
После возобновления полный baseline на host Linux с Node 18.19.1 (поддерживается src/package.json) прошёл: **238/238** за 14.1 с. Исходные тесты и таймауты не изменены. Runtime-файлы зависимостей из исходного образа доступны; ошибки распаковки касались hard-linked license-файлов. Лог: baseline-host-resumed.log. Compose config пока не проверен.
Ни один живой сервер и его firewall не изменялись; образы не публиковались.

## Решение по переходу

Аудит и полный baseline завершены. Пользователь разрешил продолжить диагностику среды; baseline прошёл без изменения тестов. Этап 1: InstanceConfig и изоляция ownership RuntimeApplier/NftablesPolicy; **242/242** теста прошли. Запуск двух контейнеров, WG backend и финальная приёмка относятся к следующим этапам.

## Этап 2

Добавлены VpnBackend/UserspaceWireGuard, отдельная схема WG v3, общие
ClientManager/Bootstrap/диагностика с выбором backend. AWG остаётся v1/v2.
Собраны локальные target awg и wg. Полный набор: **246/246**. Проверен реальный
Linux WG client → server handshake и HTTP к своей панели, TUN/userspace и
healthcheck. Версия amneziawg-go --version содержит старый snapshot 0.0.20250522;
это не показатель поколения протокола. AWG tools проверены на наличие полей
header protection, content padding, random trailers и disable cookies; точный
Go commit берётся из сборки. Полный Dual firewall/полевой Windows ещё не проверен.

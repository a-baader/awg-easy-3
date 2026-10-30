# Third-party notices

AWG-Easy 3 contains or downloads software distributed under licenses separate
from the project's CC BY-NC-SA 4.0 license. Those components retain their own
copyright notices and license terms; inclusion does not relicense them.

## Bundled AWG components

| Component | Pinned revision | License | Source |
| --- | --- | --- | --- |
| AmneziaWG Go | `b5928efb6ca19f0153958460c3d141f04abc5c2e` | MIT | [amnezia-vpn/amneziawg-go](https://github.com/amnezia-vpn/amneziawg-go) |
| AmneziaWG Tools | `ee0f0a9aa34ff0a0da4b3433b9512781cfe02843` | GPL-2.0 | [amnezia-vpn/amneziawg-tools](https://github.com/amnezia-vpn/amneziawg-tools) |

The container build compiles these exact revisions. Their license files and
source repositories are the authoritative terms for those components.

## JavaScript production dependencies

The direct production dependencies are `bcryptjs` and `qrcode`, both under the
MIT license. Their locked transitive production dependencies are:

- MIT: `ansi-regex`, `ansi-styles`, `camelcase`, `color-convert`, `color-name`,
  `decamelize`, `dijkstrajs`, `emoji-regex`, `find-up`,
  `is-fullwidth-code-point`, `locate-path`, `p-limit`, `p-locate`, `p-try`,
  `path-exists`, `pngjs`, `string-width`, `strip-ansi`, `wrap-ansi`, `y18n`,
  and `yargs`;
- ISC: `cliui`, `get-caller-file`, `require-directory`,
  `require-main-filename`, `set-blocking`, `which-module`, and `yargs-parser`.

Exact versions and integrity hashes are recorded in `src/pnpm-lock.yaml`.

## Container base images

The image is built from digest-pinned official Node.js/Alpine images. Packages
installed from Alpine retain the licenses published with their corresponding
Alpine package sources. The resulting image must be redistributed in
accordance with those package licenses as well as the licenses listed above.


## Официальные компоненты WireGuard (образ wg)

| Компонент | Закреплённая ревизия | Лицензия | Исходники |
| --- | --- | --- | --- |
| wireguard-go 0.0.20250522 | `f333402bd9cbe0f3eeb02507bd14e23d7d639280` | MIT | https://git.zx2c4.com/wireguard-go/ |
| wireguard-tools 1.0.20250521 | `e2ecaaa739144997ccff89d6ad6ec81698ea6ced` | GPL-2.0 | https://git.zx2c4.com/wireguard-tools/ |

В образ WG копируются оригинальные LICENSE/COPYING. Протокол и исходники этих
компонентов не изменяются. Их точные исходники доступны по указанным commit.
Для обоих Go-движков при сборке применяются обновлённые `go.mod`/`go.sum` из
`scripts/engine-dependencies`: закреплённые версии и контрольные суммы
security-зависимостей. Поэтому бинарные файлы не заявляются как побайтовая
сборка неизменённых upstream lock-файлов. Компилятор — Go 1.27.3.
Изменения wg-dual-easy основаны на alexsemenovru/awg-easy-3 v0.1.6;
атрибуция исходного проекта и условия CC BY-NC-SA 4.0 сохраняются.

# wg-dual-easy

[English](README.md) | [Русский](README.ru.md)

## What is wg-dual-easy?

A small Docker web panel for AmneziaWG and standard WireGuard, with two independent VPN services and one `vpnctl` command. An independent, non-commercial fork of [AWG-Easy 3](https://github.com/alexsemenovru/awg-easy-3).

> Current release: **0.2.4**. Existing clients, keys and passwords are retained during updates. See the [release notes](docs/releases/v0.2.4.md).

## At a glance

- Create and export VPN profiles in a browser; access each panel through its own VPN.
- Build a private network with Home clients, or give Guest clients Internet-only access.
- Manage both services with one command, with backup, diagnostics and rollback.

## Installation and first login

Requirements: an amd64/x86_64 Linux VPS, root access, Git, `/dev/net/tun` and available inbound UDP ports. WSL, macOS and BSD are unsupported. The installer provisions missing Docker Engine, Docker Compose v2, iproute2, nftables and Python 3 using the supported package manager.

```bash
git clone https://github.com/a-baader/wg-dual-easy.git
cd wg-dual-easy
sudo ./vpnctl install --host PUBLIC_IP_OR_DOMAIN --lang en
```

Replace `PUBLIC_IP_OR_DOMAIN` with your VPS address. Ready-made images are downloaded automatically; no server-side build is needed. Allow the selected UDP ports in the VPS/provider firewall: AWG uses a free random port, WG defaults to `51824`. The web panel ports do not need public access.

The installer shows its progress, two separate panel passwords and the first Home profiles:

- **AmneziaWG:** import the `vpn://` link into a compatible AmneziaVPN client, or use `/etc/vpn-easy-dual/bootstrap/awg.conf` with an AWG 3.x client. Connect and open `http://10.8.0.1:51821`.
- **WireGuard:** import `/etc/vpn-easy-dual/bootstrap/wg.conf` into the standard WireGuard app. Connect and open `http://10.9.0.1:51825`.

Use the password for the corresponding panel. Custom internal addresses printed by the installer take precedence. Home clients can access their own panel; Guest clients and the other VPN cannot.

If you missed a profile, export the same client again:

```bash
sudo vpnctl client export --component awg --name admin --format vpn-url
sudo vpnctl client export --component wg --name admin --format conf --output /root/wg-admin.conf
```

Passwords and bootstrap links are shown in the terminal. The public installation report is saved to `/root/wg-dual-easy-install-result.json` with mode `0600`, without passwords or profile contents. Use `--json` for JSON in stdout instead of a report file.

### Add the panel as an app

Connect your Home VPN, open the panel and use the browser's “Install app”, “Add to home screen” or “Create shortcut” menu. This does not install or start the VPN. Automatic installation prompts usually require HTTPS; the default private HTTP address may only support a shortcut. No offline profile cache is added.

## Update and management

```bash
sudo vpnctl update
```

The command checks the latest stable release and updates its source code and ready-made images, with a snapshot and rollback on failure. Clients, keys, passwords and ports are retained. There is no change if the release is already installed or no stable release is available. RC releases and downgrades are not selected. Container replacement can briefly interrupt the VPN connection.

Run `sudo vpnctl` from any directory for the management menu, or use `sudo vpnctl help`. Commands print readable text and save a public JSON report in `/root`; `--json` explicitly selects JSON in stdout. Progress and prompts go to stderr.

On systemd/OpenRC, installation enables one stable-release check after boot with a five-minute delay; systemd adds up to two minutes of random delay. Manage it with `sudo vpnctl auto-update status|enable|disable|run`. The boot check and `vpnctl update` use the same source-code and ready-made-image update process.

`vpnctl uninstall all` removes the services and working data while retaining internal backups. `vpnctl purge` also deletes internal backups and unused images whose ownership is confirmed. `vpnctl reinstall` creates a clean installation with new keys and passwords. Deletion and clean reinstallation require explicit confirmation, or `--yes` in automation.

Only application-owned resources are removed. Docker itself, foreign containers and the host's current forwarding values are preserved. Source directories and external reports/exports remain. The installed `vpnctl` is removed with the last service; use the source `./vpnctl` to install or restore afterwards.

## Technical features

- Separate AWG/WG containers, keys, clients, authentication and VPN-only panels.
- Native `.conf` and QR export; AWG also supports `vpn://` export and QR.
- Home/Guest isolation, per-client IPv4/IPv6 permissions and scoped IPv6 NAT when available.
- Live transfer rates, handshake/endpoint diagnostics and optional client packet probes.
- Consistent backup/restore, controlled migration, transaction recovery and rollback.

Detailed documentation: [installation](docs/INSTALL_DUAL.ru.md), [CLI](docs/CLI.ru.md), [output and progress](docs/CLI_OUTPUT.ru.md), [network security](docs/NETWORK_SECURITY.ru.md), [updates](docs/UPDATE_ROLLBACK.ru.md), [build](docs/BUILD.md), [test report](docs/TEST_REPORT.md). See the [documentation index](docs/README.md) for all materials.

## License and attribution

Non-commercial use under [CC BY-NC-SA 4.0](LICENSE). See [NOTICE](NOTICE) and [third-party notices](docs/THIRD_PARTY_NOTICES.md). Independent of AmneziaVPN, WireGuard and their maintainers.

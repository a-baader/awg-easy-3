#!/bin/sh
# Совместимый вход в установку: все операции выполняет единый vpnctl.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$project_dir/vpnctl" install "$@"

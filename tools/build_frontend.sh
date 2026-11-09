#!/bin/sh
set -eu

pak_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$pak_script_dir/../frontend"
pnpm install --offline --frozen-lockfile
pnpm test
pnpm run build

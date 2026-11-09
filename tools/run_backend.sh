#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
    printf '%s\n' 'Usage: sh tools/run_backend.sh /absolute/server.toml /absolute/server.crt /absolute/server.key' >&2
    exit 2
fi

pak_script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
pak_project_dir=$(dirname -- "$pak_script_dir")
export PYTHONPATH="$pak_project_dir/backend/src${PYTHONPATH:+:$PYTHONPATH}"

exec "${PAK_PYTHON:-python3}" -m pak_maintenance \
    --config "$1" --certificate "$2" --private-key "$3"

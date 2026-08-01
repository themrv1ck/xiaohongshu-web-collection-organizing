#!/bin/sh

set -e

resolve_from_extra_paths() (
  [ -n "${WORKBUDDY_EXTRA_PATHS:-}" ] || exit 1
  IFS=:
  for candidate_dir in $WORKBUDDY_EXTRA_PATHS; do
    if [ -n "$candidate_dir" ] && [ -x "$candidate_dir/node" ]; then
      printf '%s\n' "$candidate_dir/node"
      exit 0
    fi
  done
  exit 1
)

node_bin=$(resolve_from_extra_paths || command -v node || true)

if [ -z "$node_bin" ]; then
  echo "[xiaohongshu-organizer] FATAL: cannot locate node" >&2
  exit 127
fi

if [ "$#" -lt 1 ]; then
  echo "[xiaohongshu-organizer] FATAL: missing MCP server entrypoint" >&2
  exit 64
fi

if [ -z "${WORKBUDDY_CONFIG_DIR:-}" ]; then
  echo "[xiaohongshu-organizer] FATAL: WORKBUDDY_CONFIG_DIR is not set" >&2
  exit 78
fi

server_entry=$1
shift
plugin_data="${WORKBUDDY_CONFIG_DIR}/plugins/data/xiaohongshu-organizer-xiaohongshu-skill-marketplace"

exec "$node_bin" "$server_entry" "$plugin_data" "$@"

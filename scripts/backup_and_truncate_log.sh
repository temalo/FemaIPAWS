#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

SOURCE_FILE="${1:-${IPAWS_LOG_FILE:-/var/log/ipaws_meshtastic.log}}"
BACKUP_DIR="${2:-${IPAWS_LOG_BACKUP_DIR:-/var/backups/ipaws-meshtastic-logs}}"
RETENTION_DAYS="${3:-${IPAWS_LOG_RETENTION_DAYS:-14}}"

if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
    echo "Retention days must be a non-negative integer: $RETENTION_DAYS" >&2
    exit 1
fi

if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "Log file not found: $SOURCE_FILE" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date +'%Y-%m-%d_%H-%M-%S')"
base_name="$(basename "$SOURCE_FILE")"
backup_file="$BACKUP_DIR/${base_name}.${timestamp}.bak"

cp -p "$SOURCE_FILE" "$backup_file"
truncate -s 0 "$SOURCE_FILE"

deleted_count=0
if [[ "$RETENTION_DAYS" -gt 0 ]]; then
    while IFS= read -r _; do
        ((deleted_count += 1))
    done < <(find "$BACKUP_DIR" -type f -name "$base_name.*.bak" -mtime "+$RETENTION_DAYS" -delete -print)
fi

echo "Backed up $SOURCE_FILE to $backup_file, truncated the source log, and removed $deleted_count expired backup(s)."
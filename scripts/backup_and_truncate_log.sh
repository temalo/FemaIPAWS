#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_CANDIDATES=(
    "${IPAWS_ENV_FILE:-}"
    "$REPO_ROOT/.env"
    "$SCRIPT_DIR/.env"
    "$PWD/.env"
)

loaded_env_file=""

for env_file in "${ENV_CANDIDATES[@]}"; do
    if [[ -n "$env_file" && -f "$env_file" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
        loaded_env_file="$env_file"
        break
    fi
done

SOURCE_FILE="${1:-${IPAWS_LOG_FILE:-${LOG_FILE:-/var/log/ipaws_meshtastic.log}}}"
BACKUP_DIR="${2:-${IPAWS_LOG_BACKUP_DIR:-${IPAWS_BACKUP_DIR:-${BACKUP_DIR:-/var/backups/ipaws-meshtastic-logs}}}}"
RETENTION_DAYS="${3:-${IPAWS_LOG_RETENTION_DAYS:-${IPAWS_LOG_RETENTION:-${LOG_RETENTION_DAYS:-14}}}}"

if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
    echo "Retention days must be a non-negative integer: $RETENTION_DAYS" >&2
    exit 1
fi

if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "Log file not found: $SOURCE_FILE" >&2
    exit 1
fi

if [[ -n "$loaded_env_file" ]]; then
    echo "Loaded dotenv file: $loaded_env_file"
else
    echo "No dotenv file loaded; using environment variables and script defaults."
fi

echo "Using log file: $SOURCE_FILE"
echo "Using backup dir: $BACKUP_DIR"
echo "Using retention days: $RETENTION_DAYS"

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
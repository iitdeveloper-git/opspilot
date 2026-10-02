#!/usr/bin/env bash
# =============================================================================
#  OpsPilot — Database Backup Utility
#  Creates atomic timestamped backups of SQLite database files in data/
# =============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-data/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
mkdir -p "${BACKUP_DIR}"

echo "Starting OpsPilot database backup at ${TIMESTAMP}..."

if compgen -G "data/*.db" > /dev/null; then
  for db_file in data/*.db; do
    base_name=$(basename "$db_file" .db)
    backup_file="${BACKUP_DIR}/${base_name}_${TIMESTAMP}.db"
    
    # Use sqlite3 .backup if available for atomic snapshot, else copy
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$db_file" ".backup '${backup_file}'"
    else
      cp "$db_file" "$backup_file"
    fi
    echo "Backed up: ${db_file} -> ${backup_file}"
  done
  echo "Backup completed successfully."
else
  echo "No .db files found in data/ to backup."
fi

# Rotate backups older than 14 days
find "${BACKUP_DIR}" -name "*.db" -type f -mtime +14 -delete 2>/dev/null || true

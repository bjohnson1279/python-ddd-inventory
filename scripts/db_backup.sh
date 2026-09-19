#!/bin/bash
# scripts/db_backup.sh
# Standardized compressed daily/weekly/monthly database snapshot automation

set -e

BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_URL=$(python -c "import os; print(os.getenv('DATABASE_URL', '').replace('+asyncpg', ''))")

if [ -z "$DB_URL" ]; then
    echo "DATABASE_URL environment variable is not set"
    exit 1
fi

mkdir -p $BACKUP_DIR

echo "Starting database backup..."
pg_dump $DB_URL -F c -f $BACKUP_DIR/inventory_$TIMESTAMP.dump
echo "Backup saved to $BACKUP_DIR/inventory_$TIMESTAMP.dump"

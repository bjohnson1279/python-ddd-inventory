#!/bin/bash
# scripts/db_restore.sh
# Restores a PostgreSQL dump

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <path-to-dump-file>"
    exit 1
fi

DUMP_FILE=$1
DB_URL=python -c "import os; print(os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/inventory').replace('+asyncpg', ''))"

echo "Restoring database from $DUMP_FILE..."
pg_restore -c -d $DB_URL $DUMP_FILE
echo "Restore completed successfully."

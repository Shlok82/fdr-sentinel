#!/bin/sh
set -e

echo "Starting FDR Sentinel container entrypoint..."

# Wait for PostgreSQL database to be reachable
echo "Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}..."
python3 - << 'EOF'
import os
import sys
import time
import psycopg

db_host = os.getenv("DB_HOST", "db")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("POSTGRES_DB", "fdr_sentinel")
db_user = os.getenv("POSTGRES_USER", "fdr_owner")
db_password = os.getenv("POSTGRES_PASSWORD", "fdr_owner_dev_password")

conn_str = f"host={db_host} port={db_port} dbname={db_name} user={db_user} password={db_password}"
max_retries = 30
for i in range(max_retries):
    try:
        with psycopg.connect(conn_str, connect_timeout=3) as conn:
            print(f"Connected to PostgreSQL successfully on attempt {i+1}!")
            sys.exit(0)
    except Exception as e:
        print(f"Attempt {i+1}/{max_retries}: PostgreSQL not ready yet ({e}), retrying in 1s...")
        time.sleep(1)

print("Error: Timed out waiting for PostgreSQL.")
sys.exit(1)
EOF

# Run database migrations as the owner role
echo "Applying database migrations (Alembic)..."
alembic upgrade head
echo "Database migrations applied successfully."

# Start the FastAPI application
echo "Starting FDR Sentinel FastAPI application on port 8000..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

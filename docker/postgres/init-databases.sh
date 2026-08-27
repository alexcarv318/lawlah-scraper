#!/bin/bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<SQL
SELECT 'CREATE DATABASE raw_source'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'raw_source')\gexec

SELECT 'CREATE DATABASE knowledge_base'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'knowledge_base')\gexec
SQL

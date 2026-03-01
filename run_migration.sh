#!/bin/sh
# Add user_id to bank_accounts (run once)
set -e
cd "$(dirname "$0")"
docker compose exec -T db psql -U fin_savvy_user -d fin_savvy <<'SQL'
ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
UPDATE bank_accounts SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL;
ALTER TABLE bank_accounts ALTER COLUMN user_id SET NOT NULL;
SQL
echo "Migration done. Restart the app: docker compose restart app"

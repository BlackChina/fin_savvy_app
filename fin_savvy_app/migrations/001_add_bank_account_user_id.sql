-- Run this ONCE if you have an existing bank_accounts table without user_id.
-- Connect to your DB (e.g. docker compose exec db psql -U fin_savvy_user -d fin_savvy)
-- and run these statements.

-- Add column (nullable first so existing rows don't fail)
ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

-- Backfill: assign all existing accounts to the first user (e.g. mfundo)
UPDATE bank_accounts SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL;

-- Make non-null
ALTER TABLE bank_accounts ALTER COLUMN user_id SET NOT NULL;

-- =============================================================================
-- Supabase PostgreSQL Setup Script for Odoo 19 Community Edition on Vercel
-- Run this once in the Supabase Dashboard -> SQL Editor BEFORE initializing Odoo.
-- =============================================================================

-- 1. Enable PostgreSQL extensions used by Odoo 19
--    (unaccent for accent-insensitive search, pg_trgm for trigram indexes)
CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public;
CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;

-- 2. Create the shared HTTP session store table for stateless Vercel functions
--    (Also auto-created lazily by the `vercel_supabase` addon if skipped here)
CREATE TABLE IF NOT EXISTS public.odoo_vercel_sessions (
    sid VARCHAR(128) PRIMARY KEY,
    identifier VARCHAR(64) NOT NULL,
    data JSONB NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odoo_vercel_sessions_identifier
    ON public.odoo_vercel_sessions (identifier);

CREATE INDEX IF NOT EXISTS idx_odoo_vercel_sessions_updated_at
    ON public.odoo_vercel_sessions (updated_at);

-- 3. Create the Supabase Storage garbage-collection checklist table
CREATE TABLE IF NOT EXISTS public.odoo_vercel_filestore_gc (
    fname TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Create private Supabase Storage bucket for Odoo attachments & web assets
--    (Only needed when ODOO_FILESTORE_MODE=supabase; safe to run regardless)
INSERT INTO storage.buckets (id, name, public)
VALUES ('odoo-filestore', 'odoo-filestore', false)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 5. OPTIONAL (RECOMMENDED FOR VERCEL HOBBY / FREE TIER):
--    Run Odoo scheduled actions (ir.cron) every 5 minutes using Supabase
--    pg_cron + pg_net (since Vercel Hobby crons only run once per day).
--
--    Uncomment and replace <YOUR-VERCEL-DOMAIN> and <YOUR-CRON-SECRET>:
-- =============================================================================
-- CREATE EXTENSION IF NOT EXISTS pg_cron;
-- CREATE EXTENSION IF NOT EXISTS pg_net;
--
-- SELECT cron.schedule(
--     'odoo-ir-cron-every-5m',
--     '*/5 * * * *',
--     $$
--     SELECT net.http_get(
--         url := 'https://<YOUR-VERCEL-DOMAIN>.vercel.app/api/cron',
--         headers := jsonb_build_object(
--             'Authorization', 'Bearer <YOUR-CRON-SECRET>'
--         ),
--         timeout_milliseconds := 60000
--     );
--     $$
-- );

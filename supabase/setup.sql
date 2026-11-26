-- =============================================================================
-- Supabase PostgreSQL Setup Script for Odoo 19 Community Edition on Vercel
-- Run this once in the Supabase Dashboard -> SQL Editor BEFORE initializing Odoo.
-- =============================================================================

-- 1. Enable PostgreSQL extensions in `extensions` schema (Supabase best practice)
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;

-- Provide an IMMUTABLE 1-arg `public.unaccent(text)` wrapper so Odoo 19's
-- `has_unaccent()` detects INDEXABLE status while keeping extensions out of `public`.
CREATE OR REPLACE FUNCTION public.unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = extensions
AS $$
  SELECT extensions.unaccent('extensions.unaccent'::regdictionary, $1);
$$;

-- 2. Prevent PostgREST `anon` / `authenticated` roles from receiving default
--    privileges on Odoo ERP tables created by `postgres` in `public`.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- 3. Shared HTTP session store table for stateless Vercel Fluid Compute functions
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

ALTER TABLE public.odoo_vercel_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.odoo_vercel_sessions FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.odoo_vercel_sessions TO postgres, service_role;

-- 4. Supabase Storage garbage-collection checklist table
CREATE TABLE IF NOT EXISTS public.odoo_vercel_filestore_gc (
    fname TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.odoo_vercel_filestore_gc ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.odoo_vercel_filestore_gc FROM PUBLIC, anon, authenticated;
GRANT ALL ON TABLE public.odoo_vercel_filestore_gc TO postgres, service_role;

-- 5. Create private Supabase Storage bucket for Odoo attachments & web assets
INSERT INTO storage.buckets (id, name, public)
VALUES ('odoo-filestore', 'odoo-filestore', false)
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- 6. OPTIONAL (RECOMMENDED FOR VERCEL HOBBY / FREE TIER):
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

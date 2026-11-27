# Odoo 19 Community Edition — Vercel + Supabase Edition

This workspace contains **Odoo 19.0 Community Edition** (`odoo/odoo`, branch `19.0`) converted and configured to deploy on **Vercel Python Functions (Fluid Compute)** backed by **Supabase PostgreSQL + Supabase Storage**.

---

## What Was Converted

Standard Odoo 19 assumes a persistent local disk, long-running daemon processes, local session files, and an unshared local filestore. The following components adapt Odoo 19 for Vercel + Supabase:

| Component | Path | Purpose |
| :--- | :--- | :--- |
| **Vercel WSGI Entrypoint** | `api/index.py` | Bootstraps Odoo 19 (`odoo.http.root`) from env vars (`DATABASE_URL`, Vercel's `POSTGRES_URL_NON_POOLING`, or `SUPABASE_DB_*`), sets `proxy_mode=True`, `max_cron_threads=0`, `list_db=False`, `data_dir=/tmp/odoo-data`, and exposes `/api/health`, `/api/cron`, `/api/init-db`, and a clean `/websocket` guard. |
| **PostgreSQL Session Store** | `custom_addons/vercel_supabase/session_store.py` | Replaces Odoo 19's `FilesystemSessionStore` with `SupabasePostgresSessionStore` backed by `public.odoo_vercel_sessions` in Supabase PostgreSQL (full Odoo 19 soft/hard rotation, device-log identifier tracking, and vacuuming). |
| **Supabase Storage / DB Filestore** | `custom_addons/vercel_supabase/models/ir_attachment.py` | Overrides `ir.attachment` (`_storage`, `_file_write`, `_file_read`, `_file_delete`, `_gc_file_store`, and `_to_http_stream`) to persist attachments & compiled JS/CSS assets in **Supabase Storage** (with `/tmp` read-through caching on warm Fluid Compute instances) or directly in **Supabase PostgreSQL** (`ODOO_FILESTORE_MODE=db`). |
| **Serverless PDF Engine (Sales/Pro-Forma/Invoices)** | `custom_addons/vercel_supabase/models/ir_actions_report.py` | Overrides `ir.actions.report` so printing & emailing **Quotations**, **Pro-Forma Invoices**, and **Customer Invoices** generates real PDFs on Vercel without `wkhtmltopdf`: uses built-in pure-Python `xhtml2pdf` + ReportLab out of the box (with in-process logo/barcode resolution), or optional pixel-perfect Chromium/Gotenberg/Browserless via `ODOO_PDF_SERVICE_URL`. |
| **Isange Hardware ERP Suite** | `custom_addons/isange_property_fleet/` | Custom Odoo 19 ERP module (`v19.0.2.2.0`) for **Isange Hardware Ltd** (Gisozi, Gasabo District, Kigali — TIN `109047885`): Command Center, Quick 20s Sale Terminal (Cash / MTN MoMo / Bank split settlement), 2-pane Stock & Inventory Control, 2-pane Vehicle & Fleet Control (Income, Maintenance, Travels & P&L), Commercial Building Rentals & Leases, 10 studio-photographed products, and branded Pro-Forma/Invoice PDFs with 6 official RWF/USD payment accounts in the footer. |
| **Vercel Config & Ignore Rules** | `vercel.json`, `.vercelignore`, `.python-version` | Enables **Fluid Compute** (`fluid: true`), sets `maxDuration: 300`, routes all paths to `/api/index.py`, pins **Python 3.12**, excludes tests/docs/packaging bloat, and configures a Hobby-compatible Vercel Cron job. |
| **Vercel Linux Dependencies** | `requirements.txt` (original saved as `requirements.odoo.txt`) | Replaces source-compiled `psycopg2` with prebuilt `psycopg2-binary`, upgrades `rjsmin` to `1.2.2` (prebuilt cp312 manylinux wheel), removes `python-ldap` (needs `libldap2-dev`), and removes Windows-only packages. |
| **Supabase SQL Setup** | `supabase/setup.sql` | Enables `unaccent` and `pg_trgm` extensions, creates `odoo_vercel_sessions` & `odoo_vercel_filestore_gc`, creates the private `odoo-filestore` Storage bucket, and provides an optional `pg_cron` + `pg_net` snippet for every-5-minute Odoo crons even on Vercel Hobby. |
| **DB Init & Bundle Pruning CLIs** | `scripts/init_supabase_db.py`, `scripts/prune_addons.py` | Local/CI database initializer and optional addon/translation pruner to stay under 500 MB without Large Functions. |

---

## Step-by-Step Deployment Guide

### 1. Prepare Supabase

1. Create a Supabase project (PostgreSQL 15/16/17).
2. Open **Supabase Dashboard → SQL Editor** and execute `supabase/setup.sql`.
3. Copy your **Session Pooler** connection string from **Project Settings → Database → Connection string → Method: Session pooler (port 5432)**:
   ```text
   postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```
   > **Important:**
   > - Use **Session Pooler (port `5432`)**, which is IPv4-compatible for Vercel Lambda/Fluid Compute AND preserves PostgreSQL session state (`LISTEN`/`NOTIFY`, advisory locks, temp tables).
   > - Do **not** use Transaction Pooler (port `6543`), which breaks Odoo's session-level locks and signaling.
   > - Do **not** use the Direct connection host (`db.<ref>.supabase.co`) unless you enabled Supabase's IPv4 add-on (Vercel functions use IPv4 outbound by default).
4. From **Project Settings → API**, copy:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` secret key → `SUPABASE_SERVICE_ROLE_KEY`

### 2. Bundle Size Strategy (Fits Under Vercel's Default 500 MB Limit!)

Measured sizes of this extracted Odoo 19.0 Community Edition workspace:

- Full raw Odoo 19 (`odoo/` + `addons/`): **1,062.6 MB** (47,490 files)
- Excluding `tests/`, `demo/`, and `**/*.po` translation files (already configured in `.vercelignore` and `vercel.json`): **187.3 MB** (`odoo/`: 7.8 MB + all 400+ `addons/`: 179.5 MB)
- Plus Python 3.12 dependencies: **~310 MB total** — fits natively inside Vercel's **500 MB** standard Python function limit with **ALL 400+ Odoo 19 Community addons intact**!

*(English `en_US` strings are embedded directly in Odoo's Python/XML/JS source code and do not require `.po` files.)*

- **Need non-English translations (e.g., French, Spanish, Arabic)?**
  - Either un-ignore specific `.po` files in `.vercelignore` / `vercel.json` (e.g., `!**/*/i18n/fr.po`), **OR**
  - Remove `**/*.po` from `.vercelignore` & `vercel.json` and set `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` in Vercel Environment Variables (raises Fluid Compute bundle limit to **5 GB**).
- **Want even faster cold starts & smaller deploys for Isange Hardware ERP?**
  Use the curated `isange-erp` preset in `scripts/prune_addons.py` (keeps only the exact Odoo 19 addons required by `isange_property_fleet` — Sales, Pro-Forma, Purchase, Stock, Invoicing, POS, Fleet, Maintenance, Expenses, Spreadsheet Dashboard, Contacts, and Portal + your country localization):
  ```bash
  # Dry-run preview (replace l10n_rw with your country code, or omit --l10n)
  python scripts/prune_addons.py --preset isange-erp --langs en --dry-run

  # Apply pruning
  python scripts/prune_addons.py --preset isange-erp --langs en --apply
  ```

### 3. Deploy to Vercel

1. Push this directory to a Git repository (GitHub / GitLab / Bitbucket) and import it into Vercel (Framework Preset: **Other**).
2. In **Vercel Project Settings → Environment Variables**, configure the variables from `.env.example`:
   - `DATABASE_URL` (Supabase Session Pooler port 5432 URI)
   - `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` *(if using Option A)*
   - `ODOO_FILESTORE_MODE=supabase` *(or `db` to skip Supabase Storage and store attachments directly in PostgreSQL)*
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET=odoo-filestore` *(when using `supabase` mode)*
   - `ODOO_DB_MAXCONN=4`
   - `ODOO_ADMIN_PASSWD=<strong-random-string>`
   - `CRON_SECRET=<32-byte-random-hex>`
   - `ODOO_INIT_SECRET=<32-byte-random-hex>` *(temporary — remove after DB init!)*
   - `ODOO_DEFAULT_ADMIN_LOGIN=admin`
   - `ODOO_DEFAULT_ADMIN_PASSWORD=<your-admin-password>`
3. Deploy the project.
4. Verify connectivity:
   ```bash
   curl https://<your-project>.vercel.app/api/health
   ```
   You should see `"db_connected": true` and `"db_initialized": false`.

### 4. Initialize Odoo 19 Inside Supabase PostgreSQL

You have two ways to initialize the `postgres` database schema:

#### Option A — Directly via Vercel (no local Python required)
To stay comfortably inside Vercel's 300s function timeout over remote Supabase connections, run initialization in three quick batches (installs the full **Isange Hardware ERP** suite and automatically enables `Send PRO-FORMA Invoice`):
```bash
# Step 1: Core Odoo 19 + Vercel/Supabase adapter (~30-60s)
curl -X POST "https://<your-project>.vercel.app/api/init-db" \
  -H "Authorization: Bearer <YOUR_ODOO_INIT_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"modules": ["base", "web", "vercel_supabase"], "lang": "en_US", "demo": false}'

# Step 2: Contacts, Sales, Purchase, Stock & Invoicing (~60-180s)
curl -X POST "https://<your-project>.vercel.app/api/init-db" \
  -H "Authorization: Bearer <YOUR_ODOO_INIT_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"action": "init", "modules": ["contacts", "sale_management", "purchase", "stock", "account"]}'

# Step 3: POS, Fleet, Maintenance, Expenses & Isange Hardware ERP (~60-180s)
curl -X POST "https://<your-project>.vercel.app/api/init-db" \
  -H "Authorization: Bearer <YOUR_ODOO_INIT_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"action": "init", "modules": ["point_of_sale", "fleet", "maintenance", "hr_expense", "isange_property_fleet"]}'
```
*(Optional: include `"l10n_rw"` in a separate call if you also want Rwanda's full statutory chart of accounts.)*

> **Security:** Once your database is initialized, delete `ODOO_INIT_SECRET` from Vercel Environment Variables (this automatically disables `/api/init-db`).

#### Option B — From Local Machine or CI (Python 3.12 — Recommended for One-Shot Init)
```bash
pip install -r requirements.txt
python scripts/init_supabase_db.py --admin-password "YourStrongPassword!"
```
*(Installs `base,web,contacts,sale_management,purchase,stock,account,point_of_sale,fleet,maintenance,hr_expense,isange_property_fleet,vercel_supabase`, loads Isange Hardware Ltd company/bank/product data, and enables Pro-Forma Invoices automatically.)*

### 5. Sign In & Configure Scheduled Actions (`ir.cron`)

1. Open `https://<your-project>.vercel.app/odoo` (or `/web/login`) and sign in with your configured admin login and password.
2. **Scheduled Actions (`ir.cron`):**
   - `vercel.json` includes a daily cron (`0 3 * * *`) hitting `/api/cron` so it deploys cleanly on Vercel Hobby.
   - **Vercel Pro:** Change `"schedule"` in `vercel.json` to `"*/5 * * * *"` (every 5 minutes).
   - **Vercel Hobby (Free):** Uncomment Section 5 in `supabase/setup.sql` to run Supabase `pg_cron` + `pg_net` every 5 minutes against `https://<your-project>.vercel.app/api/cron` with your `CRON_SECRET`.

---

## Architecture Notes & Known Limitations

Because Vercel is a serverless/Fluid Compute HTTP platform rather than a traditional stateful Linux server, keep these limitations in mind before using this setup in production:

1. **No Persistent WebSockets (`/websocket`):**
   Vercel Functions cannot hold long-lived WebSocket connections (`addons/bus`). `api/index.py` returns HTTP `503` on `/websocket` cleanly so requests do not hang. Odoo Discuss live push notifications, Livechat, and real-time bus updates will not work over WebSockets.
2. **PDF Report Generation (Quotations, Pro-Forma Invoices & Invoices — Supported!):**
   - **Out of the box (`ODOO_PDF_ENGINE=auto`):** `custom_addons/vercel_supabase/models/ir_actions_report.py` renders QWeb reports to real PDFs using pure-Python **`xhtml2pdf` + ReportLab**, resolving company logos, barcodes, and attachments in-process without `wkhtmltopdf` or external services. Printing and emailing Quotations, Pro-Forma Invoices, and Customer Invoices works immediately on Vercel.
   - **Pixel-perfect Bootstrap 5 upgrade (optional):** Because `xhtml2pdf` uses a CSS 2.1 engine (not full Chromium flexbox/grid), complex custom Bootstrap 5 QWeb themes may look slightly simpler than native Chromium/wkhtmltopdf output. For 100% pixel-identical Odoo invoice styling, set `ODOO_PDF_SERVICE_URL` to a hosted **Gotenberg**, **Browserless**, or **Cloudflare Browser Rendering** endpoint (`ODOO_PDF_SERVICE_TYPE=gotenberg|browserless|cloudflare`).
3. **HTTP Request / Response Payload Limits:**
   Standard Vercel Functions enforce a **4.5 MB** request body limit (expandable to **20 MiB** on Fluid Compute Performance 4 GB tier with `VERCEL_SUPPORT_LARGE_REQUEST_BODY=1`). Large binary uploads (>4.5 MB) through Odoo will hit HTTP `413`.
4. **Cold Starts & Memory:**
   Cold-starting an Odoo 19 `Registry` typically takes 3–15 seconds depending on the number of installed addons. Fluid Compute keeps warm instances alive across concurrent requests, dramatically reducing cold starts once warm. Keep `ODOO_DB_MAXCONN` small (`3`–`6`) so parallel Fluid Compute instances do not exhaust Supabase's session pooler connections.
5. **Supabase `postgres` Database Naming Gotcha (Handled Automatically):**
   In Odoo 19 (`odoo/service/db.py`), setting `--db-filter` causes `list_dbs()` to query `pg_database` while explicitly excluding `'postgres'`. `api/index.py` intentionally omits `--db-filter` and passes `--database=postgres` + `--no-database-list`, making Odoo expose Supabase's `postgres` database directly.
6. **When to Consider Container Hosting Instead:**
   If you need full production ERP features—WebSockets/Discuss/Livechat, native `wkhtmltopdf` PDF printing, large file uploads, heavy accounting/inventory batch crons, and zero cold starts—deploy Odoo 19 in Docker on **Railway, Render, Fly.io, or a VPS** (still backed by Supabase PostgreSQL if desired), and optionally host a custom Next.js frontend/portal on Vercel calling Odoo's JSON-RPC API.

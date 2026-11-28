# -*- coding: utf-8 -*-
"""
Vercel Fluid Compute Python WSGI Entrypoint for Odoo 19 Community Edition + Supabase.

Exposes standard WSGI callables ``app`` and ``application`` backed by Odoo 19's
``odoo.http.root`` plus protected platform management endpoints:
  - GET  /api/health   : Supabase DB connectivity & Odoo initialization status
  - GET  /api/cron     : Trigger Odoo ``ir.cron`` jobs & session vacuum (CRON_SECRET)
  - POST /api/init-db  : Initialize Supabase DB or install/upgrade addons (ODOO_INIT_SECRET)
  - ANY  /websocket    : Fast serverless-safe response (WebSockets are unsupported on Vercel)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# Ensure repository root is importable before importing `odoo`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_logger = logging.getLogger('odoo.vercel')
_BOOT_LOCK = threading.Lock()
_BOOTSTRAPPED = False
_BOOT_CONFIG_META: dict = {}


# =============================================================================
# 1. Supabase PostgreSQL Configuration Resolution
# =============================================================================
def _resolve_db_config() -> dict:
    """Resolve PostgreSQL connection parameters from Vercel / Supabase env vars.

    Priority:
      1. DATABASE_URL / SUPABASE_DB_URL
      2. POSTGRES_URL_NON_POOLING (Vercel's official Supabase integration, session mode)
      3. POSTGRES_URL (fallback; warns if port 6543 transaction pooler is detected)
      4. Individual SUPABASE_DB_* / POSTGRES_* / PG* variables
    """
    raw_uri = (
        os.environ.get('DATABASE_URL')
        or os.environ.get('SUPABASE_DB_URL')
        or os.environ.get('POSTGRES_URL_NON_POOLING')
        or os.environ.get('POSTGRES_URL')
        or ''
    ).strip()

    cfg = {
        'host': os.environ.get('SUPABASE_DB_HOST')
                or os.environ.get('POSTGRES_HOST')
                or os.environ.get('PGHOST')
                or '',
        'port': os.environ.get('SUPABASE_DB_PORT')
                or os.environ.get('POSTGRES_PORT')
                or os.environ.get('PGPORT')
                or '5432',
        'name': os.environ.get('SUPABASE_DB_NAME')
                or os.environ.get('POSTGRES_DATABASE')
                or os.environ.get('PGDATABASE')
                or 'postgres',
        'user': os.environ.get('SUPABASE_DB_USER')
                or os.environ.get('POSTGRES_USER')
                or os.environ.get('PGUSER')
                or 'postgres',
        'password': os.environ.get('SUPABASE_DB_PASSWORD')
                    or os.environ.get('POSTGRES_PASSWORD')
                    or os.environ.get('PGPASSWORD')
                    or '',
        'sslmode': os.environ.get('SUPABASE_DB_SSLMODE')
                   or os.environ.get('PGSSLMODE')
                   or 'require',
        'source': 'env_fields',
    }

    if raw_uri:
        parsed = urlparse(raw_uri)
        qs = parse_qs(parsed.query)
        cfg['host'] = parsed.hostname or cfg['host']
        cfg['port'] = str(parsed.port or cfg['port'] or '5432')
        if parsed.path and parsed.path.strip('/'):
            cfg['name'] = unquote(parsed.path.strip('/'))
        if parsed.username:
            cfg['user'] = unquote(parsed.username)
        if parsed.password is not None:
            cfg['password'] = unquote(parsed.password)
        if 'sslmode' in qs and qs['sslmode']:
            cfg['sslmode'] = qs['sslmode'][0]
        cfg['source'] = 'uri'

    cfg['port'] = str(cfg['port'] or '5432')
    cfg['is_transaction_pooler'] = (cfg['port'] == '6543')
    return cfg


def _build_addons_path() -> str:
    candidates = [
        PROJECT_ROOT / 'custom_addons',
        PROJECT_ROOT / 'addons',
        PROJECT_ROOT / 'odoo' / 'addons',
    ]
    extra = os.environ.get('ODOO_EXTRA_ADDONS_PATH', '')
    for part in extra.split(','):
        part = part.strip()
        if part:
            candidates.append(Path(part))

    existing = []
    for path in candidates:
        resolved = str(path.resolve())
        if path.is_dir() and resolved not in existing:
            existing.append(resolved)
    return ','.join(existing)


def _prepare_ephemeral_data_dir() -> str:
    data_dir = os.environ.get('ODOO_DATA_DIR') or '/tmp/odoo-data'
    for sub in ('', 'sessions', 'filestore', 'addons'):
        os.makedirs(os.path.join(data_dir, sub) if sub else data_dir, exist_ok=True)
    return data_dir


def bootstrap_odoo() -> None:
    """Thread-safe one-time initialization of Odoo 19 config and server-wide modules."""
    global _BOOTSTRAPPED, _BOOT_CONFIG_META
    if _BOOTSTRAPPED:
        return

    with _BOOT_LOCK:
        if _BOOTSTRAPPED:
            return

        db_cfg = _resolve_db_config()
        data_dir = _prepare_ephemeral_data_dir()
        addons_path = _build_addons_path()

        if db_cfg['is_transaction_pooler']:
            _logger.warning(
                "Detected PostgreSQL port 6543 (Supabase Transaction Pooler). "
                "Odoo requires Session Mode (port 5432 on *.pooler.supabase.com) "
                "for advisory locks, LISTEN/NOTIFY, and session state."
            )

        # Ensure libpq env defaults match (especially PGSSLMODE for Supabase)
        os.environ.setdefault('PGSSLMODE', db_cfg['sslmode'])

        server_wide = (
            os.environ.get('ODOO_SERVER_WIDE_MODULES')
            or 'base,web,vercel_supabase'
        ).strip()
        if 'vercel_supabase' not in [m.strip() for m in server_wide.split(',')]:
            server_wide = f"{server_wide},vercel_supabase"

        db_maxconn = os.environ.get('ODOO_DB_MAXCONN') or '12'
        log_level = os.environ.get('ODOO_LOG_LEVEL') or 'info'

        # IMPORTANT: On Supabase the database is literally named `postgres`.
        # In Odoo 19 (`odoo/service/db.py:450-462`), setting `--db-filter` forces
        # `list_dbs()` to query `pg_database WHERE datname NOT IN ('postgres', ...)`,
        # which EXCLUDES `postgres`! Leaving `--db-filter` unset while setting
        # `--database=<db_name>` makes Odoo short-circuit and expose `[db_name]` directly.
        cli_args = [
            f"--addons-path={addons_path}",
            f"--data-dir={data_dir}",
            f"--database={db_cfg['name']}",
            f"--db_port={db_cfg['port']}",
            f"--db_user={db_cfg['user']}",
            f"--db_sslmode={db_cfg['sslmode']}",
            f"--db_maxconn={db_maxconn}",
            f"--load={server_wide}",
            f"--log-level={log_level}",
            "--max-cron-threads=0",
            "--no-database-list",
            "--proxy-mode",
            "--limit-memory-soft=0",
        ]
        if os.name == 'posix':
            cli_args.append("--limit-memory-hard=0")
        if db_cfg['host']:
            cli_args.append(f"--db_host={db_cfg['host']}")
        if db_cfg['password']:
            cli_args.append(f"--db_password={db_cfg['password']}")

        explicit_dbfilter = (os.environ.get('ODOO_DB_FILTER') or '').strip()
        if explicit_dbfilter:
            cli_args.append(f"--db-filter={explicit_dbfilter}")

        import odoo  # noqa: PLC0415
        import odoo.service.server as odoo_server  # noqa: PLC0415

        odoo.tools.config.parse_config(cli_args)

        admin_passwd = (os.environ.get('ODOO_ADMIN_PASSWD') or '').strip()
        if admin_passwd:
            odoo.tools.config['admin_passwd'] = admin_passwd

        # Load server-wide modules (base, web, vercel_supabase -> activates PG session store)
        odoo_server.load_server_wide_modules()

        _BOOT_CONFIG_META = {
            'db_name': db_cfg['name'],
            'db_host': db_cfg['host'],
            'db_port': db_cfg['port'],
            'db_sslmode': db_cfg['sslmode'],
            'db_maxconn': int(db_maxconn),
            'is_transaction_pooler': db_cfg['is_transaction_pooler'],
            'data_dir': data_dir,
            'server_wide_modules': server_wide,
        }
        _BOOTSTRAPPED = True
        _logger.info(
            "Odoo 19 bootstrapped for Vercel + Supabase (db=%s, host=%s:%s, maxconn=%s)",
            db_cfg['name'], db_cfg['host'], db_cfg['port'], db_maxconn,
        )


# =============================================================================
# 2. Helpers for JSON Responses & Bearer Auth
# =============================================================================
def _json_response(start_response, payload: dict, status: str = '200 OK'):
    body = json.dumps(payload, indent=2, default=str).encode('utf-8')
    headers = [
        ('Content-Type', 'application/json; charset=utf-8'),
        ('Content-Length', str(len(body))),
        ('Cache-Control', 'no-store'),
    ]
    start_response(status, headers)
    return [body]


def _check_bearer_secret(environ: dict, env_var_name: str) -> tuple[bool, str]:
    expected = (os.environ.get(env_var_name) or '').strip()
    if not expected:
        return False, f"Endpoint disabled: {env_var_name} environment variable is not configured."

    auth_header = (environ.get('HTTP_AUTHORIZATION') or '').strip()
    token = ''
    if auth_header.lower().startswith('bearer '):
        token = auth_header[7:].strip()
    elif environ.get('HTTP_X_CRON_SECRET'):
        token = environ['HTTP_X_CRON_SECRET'].strip()

    if not token or not hmac.compare_digest(token, expected):
        return False, "Unauthorized: invalid or missing Bearer token."
    return True, ''


def _read_json_body(environ: dict) -> dict:
    try:
        length = int(environ.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = environ['wsgi.input'].read(length)
    if not raw:
        return {}
    return json.loads(raw.decode('utf-8'))


# =============================================================================
# 3. Platform Endpoints (/api/health, /api/cron, /api/init-db, /websocket)
# =============================================================================
def _handle_health(environ: dict, start_response):
    t0 = time.monotonic()
    try:
        bootstrap_odoo()
        import odoo.http as odoo_http  # noqa: PLC0415
        import odoo.sql_db as sql_db  # noqa: PLC0415
        from odoo.addons.vercel_supabase.models.ir_attachment import _get_filestore_mode  # noqa: PLC0415
        from odoo.addons.vercel_supabase.models.ir_actions_report import get_active_pdf_engine  # noqa: PLC0415

        db_name = _BOOT_CONFIG_META['db_name']
        db_connected = False
        odoo_initialized = False
        installed_modules = 0
        pg_version = None
        db_error = None

        try:
            db = sql_db.db_connect(db_name)
            with db.cursor() as cr:
                cr.execute("SELECT version()")
                pg_version = cr.fetchone()[0]
                db_connected = True
                cr.execute("SELECT to_regclass('public.ir_module_module')")
                if cr.fetchone()[0] is not None:
                    odoo_initialized = True
                    cr.execute("SELECT COUNT(*) FROM ir_module_module WHERE state = 'installed'")
                    installed_modules = cr.fetchone()[0]
        except Exception as exc:
            db_error = str(exc)

        warnings_list = []
        if _BOOT_CONFIG_META.get('is_transaction_pooler'):
            warnings_list.append(
                "Port 6543 (Transaction Pooler) detected. Switch to Supabase Session Pooler (port 5432)."
            )
        if not odoo_initialized and db_connected:
            warnings_list.append(
                "Supabase PostgreSQL is reachable but Odoo schema is not initialized yet. "
                "Run POST /api/init-db or scripts/init_supabase_db.py."
            )

        status_code = '200 OK' if db_connected else '503 Service Unavailable'
        return _json_response(
            start_response,
            {
                'ok': db_connected,
                'odoo_version': '19.0',
                'db_name': db_name,
                'db_connected': db_connected,
                'db_initialized': odoo_initialized,
                'installed_modules': installed_modules,
                'pg_version': pg_version,
                'db_error': db_error,
                'filestore_mode': _get_filestore_mode() or 'file',
                'pdf_engine': get_active_pdf_engine(),
                'session_store': type(odoo_http.root.session_store).__name__,
                'warnings': warnings_list,
                'elapsed_ms': round((time.monotonic() - t0) * 1000, 1),
            },
            status=status_code,
        )
    except Exception as exc:
        _logger.exception("Health check failed")
        return _json_response(
            start_response,
            {'ok': False, 'error': str(exc)},
            status='500 Internal Server Error',
        )


def _handle_cron(environ: dict, start_response):
    ok, err = _check_bearer_secret(environ, 'CRON_SECRET')
    if not ok:
        status = '403 Forbidden' if 'disabled' in err else '401 Unauthorized'
        return _json_response(start_response, {'ok': False, 'error': err}, status=status)

    t0 = time.monotonic()
    try:
        bootstrap_odoo()
        import odoo.http as odoo_http  # noqa: PLC0415
        from odoo.addons.base.models.ir_cron import IrCron  # noqa: PLC0415

        db_name = _BOOT_CONFIG_META['db_name']
        IrCron._process_jobs(db_name)

        vacuumed = None
        if hasattr(odoo_http.root.session_store, 'vacuum'):
            vacuumed = odoo_http.root.session_store.vacuum()

        return _json_response(
            start_response,
            {
                'ok': True,
                'db': db_name,
                'sessions_vacuumed': vacuumed,
                'elapsed_ms': round((time.monotonic() - t0) * 1000, 1),
            },
        )
    except Exception as exc:
        _logger.exception("Cron execution failed")
        return _json_response(
            start_response,
            {
                'ok': False,
                'error': str(exc),
                'elapsed_ms': round((time.monotonic() - t0) * 1000, 1),
            },
            status='500 Internal Server Error',
        )


def _handle_init_db(environ: dict, start_response):
    if environ.get('REQUEST_METHOD', 'GET').upper() != 'POST':
        return _json_response(
            start_response,
            {'ok': False, 'error': 'Use POST /api/init-db with Authorization: Bearer <ODOO_INIT_SECRET>.'},
            status='405 Method Not Allowed',
        )

    ok, err = _check_bearer_secret(environ, 'ODOO_INIT_SECRET')
    if not ok:
        status = '403 Forbidden' if 'disabled' in err else '401 Unauthorized'
        return _json_response(start_response, {'ok': False, 'error': err}, status=status)

    t0 = time.monotonic()
    try:
        body = _read_json_body(environ)
    except Exception as exc:
        return _json_response(
            start_response,
            {'ok': False, 'error': f'Invalid JSON body: {exc}'},
            status='400 Bad Request',
        )

    try:
        bootstrap_odoo()
        import odoo  # noqa: PLC0415
        import odoo.api as api  # noqa: PLC0415
        import odoo.sql_db as sql_db  # noqa: PLC0415
        from odoo.modules.registry import Registry  # noqa: PLC0415

        db_name = _BOOT_CONFIG_META['db_name']
        action = (body.get('action') or 'init').strip().lower()

        default_mods = [
            m.strip()
            for m in (
                os.environ.get('ODOO_DEFAULT_MODULES')
                or 'base,web,contacts,sale_management,purchase,stock,account,point_of_sale,fleet,maintenance,hr_expense,isange_property_fleet,zxs_entp_theme,vercel_supabase'
            ).split(',')
            if m.strip()
        ]
        modules = body.get('modules') or default_mods
        if isinstance(modules, str):
            modules = [m.strip() for m in modules.split(',') if m.strip()]

        lang = (body.get('lang') or os.environ.get('ODOO_DEFAULT_LANG') or 'en_US').strip()
        admin_login = (body.get('admin_login') or os.environ.get('ODOO_DEFAULT_ADMIN_LOGIN') or 'admin').strip()
        admin_password = (body.get('admin_password') or os.environ.get('ODOO_DEFAULT_ADMIN_PASSWORD') or '').strip()
        demo = bool(body.get('demo', False))

        # Check if database already has Odoo schema
        already_initialized = False
        db = sql_db.db_connect(db_name)
        with db.cursor() as cr:
            cr.execute("SELECT to_regclass('public.ir_module_module')")
            already_initialized = cr.fetchone()[0] is not None
            if already_initialized:
                from odoo.modules.module import Manifest, initialize_sys_path  # noqa: PLC0415
                initialize_sys_path()
                cr.execute(
                    "SELECT id, name, state, auto_install FROM public.ir_module_module "
                    "WHERE state IN ('uninstalled', 'to install')"
                )
                missing_ids = [
                    row[0] for row in cr.fetchall()
                    if (row[2] == 'to install' or row[3])
                    and Manifest.for_addon(row[1], display_warning=False) is None
                ]
                if missing_ids:
                    cr.execute(
                        "UPDATE public.ir_module_module "
                        "SET state = 'uninstalled', auto_install = false "
                        "WHERE id = ANY(%s)",
                        (missing_ids,),
                    )
                    cr.commit()

        if action == 'upgrade':
            install_mods = ()
            upgrade_mods = tuple(modules)
        else:
            if 'vercel_supabase' not in modules:
                modules.append('vercel_supabase')
            install_mods = tuple(modules)
            upgrade_mods = ()

        registry = Registry.new(
            db_name,
            update_module=True,
            install_modules=install_mods,
            upgrade_modules=upgrade_mods,
            new_db_demo=demo,
        )

        with registry.cursor() as cr:
            if lang and lang != 'en_US':
                import odoo.tools.translate as odoo_translate  # noqa: PLC0415
                odoo_translate.load_language(cr, lang)
            env = api.Environment(cr, api.SUPERUSER_ID, {})

            # Set admin credentials on fresh initialization or when explicitly passed in body
            if (not already_initialized and admin_password) or body.get('admin_password'):
                admin_user = env.ref('base.user_admin', raise_if_not_found=False)
                if admin_user:
                    vals = {'password': admin_password}
                    if admin_login:
                        vals['login'] = admin_login
                    if lang:
                        vals['lang'] = lang
                    admin_user.write(vals)

            # Configure web.base.url if deployment URL is known
            base_url = (os.environ.get('ODOO_BASE_URL') or '').strip()
            if not base_url and os.environ.get('VERCEL_PROJECT_PRODUCTION_URL'):
                base_url = f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
            if base_url:
                env['ir.config_parameter'].sudo().set_param('web.base.url', base_url.rstrip('/'))

            # Sync ir_attachment.location parameter with active filestore mode
            from odoo.addons.vercel_supabase.models.ir_attachment import _get_filestore_mode  # noqa: PLC0415
            fs_mode = _get_filestore_mode()
            if fs_mode:
                env['ir.config_parameter'].sudo().set_param('ir_attachment.location', fs_mode)

            # Enable Pro-Forma Invoices automatically when the Sales module is installed
            proforma_group = env.ref('sale.group_proforma_sales', raise_if_not_found=False)
            user_group = env.ref('base.group_user', raise_if_not_found=False)
            if proforma_group and user_group and proforma_group not in user_group.implied_ids:
                user_group.sudo().write({'implied_ids': [(4, proforma_group.id)]})

            cr.commit()

        return _json_response(
            start_response,
            {
                'ok': True,
                'action': action,
                'db': db_name,
                'was_already_initialized': already_initialized,
                'modules': list(modules),
                'elapsed_ms': round((time.monotonic() - t0) * 1000, 1),
                'reminder': 'Remove ODOO_INIT_SECRET from Vercel env vars once initialization is complete.',
            },
        )
    except Exception as exc:
        _logger.exception("Database initialization failed")
        return _json_response(
            start_response,
            {
                'ok': False,
                'error': str(exc),
                'elapsed_ms': round((time.monotonic() - t0) * 1000, 1),
            },
            status='500 Internal Server Error',
        )


# =============================================================================
# 4. Zero-DB Static File Fast Path & Vercel Edge CDN Header Middleware
# =============================================================================
_STATIC_PATH_RE = re.compile(r'^/([A-Za-z0-9_]+)/static/(.+)$')
_STATIC_ROOTS = (
    PROJECT_ROOT / 'custom_addons',
    PROJECT_ROOT / 'addons',
    PROJECT_ROOT / 'odoo' / 'addons',
)
_STATIC_CACHE: dict[str, tuple[bytes, str, str] | None] = {}
_STATIC_CACHE_MAX = 512

mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('font/woff', '.woff')
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')


def _serve_addon_static(environ: dict, start_response, path: str):
    """Serve /<addon>/static/... directly from disk with zero Odoo/DB overhead."""
    method = environ.get('REQUEST_METHOD', 'GET').upper()
    if method not in ('GET', 'HEAD'):
        return None

    m = _STATIC_PATH_RE.match(path)
    if not m:
        return None
    addon_name, rel_part = m.group(1), unquote(m.group(2))
    if '..' in rel_part or '\x00' in rel_part:
        return None

    cache_key = f"{addon_name}/{rel_part}"
    cached = _STATIC_CACHE.get(cache_key)
    if cache_key not in _STATIC_CACHE:
        found = None
        for root in _STATIC_ROOTS:
            static_base = (root / addon_name / 'static').resolve()
            if not static_base.is_dir():
                continue
            target = (static_base / rel_part).resolve()
            try:
                target.relative_to(static_base)
            except ValueError:
                continue
            if target.is_file():
                # Only cache files up to 512 KB in RAM; stream larger ones directly
                size = target.stat().st_size
                mime, _ = mimetypes.guess_type(str(target))
                content_type = mime or 'application/octet-stream'
                data = target.read_bytes()
                etag = f'"{hashlib.sha1(data).hexdigest()}"'
                found = (data, content_type, etag)
                if size <= 524288 and len(_STATIC_CACHE) < _STATIC_CACHE_MAX:
                    _STATIC_CACHE[cache_key] = found
                break
        if found is None and len(_STATIC_CACHE) < _STATIC_CACHE_MAX:
            _STATIC_CACHE[cache_key] = None
        cached = found

    if cached is None:
        return None

    data, content_type, etag = cached
    inm = (environ.get('HTTP_IF_NONE_MATCH') or '').strip()
    base_headers = [
        ('Content-Type', content_type),
        ('ETag', etag),
        ('Cache-Control', 'public, max-age=31536000, immutable'),
        ('CDN-Cache-Control', 'public, max-age=31536000, immutable'),
        ('X-Isange-Fast-Static', '1'),
    ]
    if inm == etag:
        start_response('304 Not Modified', base_headers)
        return [b'']

    base_headers.append(('Content-Length', str(len(data))))
    start_response('200 OK', base_headers)
    return [b''] if method == 'HEAD' else [data]


def _classify_cdn_cache(path: str, query_string: str) -> str:
    """Return caching tier for Odoo-served responses:
      - 'public_immutable' : /web/assets/... (hashed bundles; strip Set-Cookie for Edge HIT!)
      - 'public_logo'      : /web/binary/company_logo, /favicon.ico (strip Set-Cookie!)
      - 'private_immutable': /web/image/...?unique=... (browser disk cache, never shared CDN)
      - ''                 : dynamic/HTML/RPC (leave untouched)
    """
    if path.startswith('/web/assets/'):
        return 'public_immutable'
    if path in ('/web/binary/company_logo', '/favicon.ico', '/web/static/img/favicon.ico'):
        return 'public_logo'
    if path.startswith('/web/image/') and 'unique=' in (query_string or ''):
        return 'private_immutable'
    return ''


def _wrap_cdn_start_response(start_response, cache_tier: str):
    if not cache_tier:
        return start_response

    def wrapped(status, headers, exc_info=None):
        code = int(str(status).split(' ', 1)[0]) if status else 500
        if code in (200, 304):
            new_headers = []
            for k, v in headers:
                kl = k.lower()
                # Removing Set-Cookie on public static/asset responses unlocks Vercel Edge CDN HITs
                if cache_tier in ('public_immutable', 'public_logo') and kl == 'set-cookie':
                    continue
                if kl in ('cache-control', 'cdn-cache-control', 'pragma', 'expires'):
                    continue
                new_headers.append((k, v))

            if cache_tier == 'public_immutable':
                new_headers.append(('Cache-Control', 'public, max-age=31536000, immutable'))
                new_headers.append(('CDN-Cache-Control', 'public, max-age=31536000, immutable'))
            elif cache_tier == 'public_logo':
                new_headers.append(('Cache-Control', 'public, max-age=3600, stale-while-revalidate=86400'))
                new_headers.append(('CDN-Cache-Control', 'public, max-age=86400, stale-while-revalidate=604800'))
            elif cache_tier == 'private_immutable':
                new_headers.append(('Cache-Control', 'private, max-age=31536000, immutable'))
            return start_response(status, new_headers, exc_info)
        return start_response(status, headers, exc_info)

    return wrapped


# =============================================================================
# 5. Main Vercel WSGI Application
# =============================================================================
def application(environ: dict, start_response):
    """WSGI entrypoint invoked by Vercel Python Fluid Compute."""
    path = environ.get('PATH_INFO', '') or '/'

    # Fast zero-DB static file serving for /<addon>/static/... (icons, fonts, JS/CSS)
    if '/static/' in path:
        static_resp = _serve_addon_static(environ, start_response, path)
        if static_resp is not None:
            return static_resp

    # Handle Vercel + Supabase platform endpoints before entering Odoo routing
    if path in ('/api/health', '/_vercel_odoo/health'):
        return _handle_health(environ, start_response)
    if path in ('/api/cron', '/_vercel_odoo/cron'):
        return _handle_cron(environ, start_response)
    if path in ('/api/init-db', '/_vercel_odoo/init-db'):
        return _handle_init_db(environ, start_response)

    # Vercel Functions do not support persistent WebSockets (`addons/bus`).
    # Respond cleanly so the Odoo frontend backs off instead of triggering 500s.
    if path == '/websocket':
        return _json_response(
            start_response,
            {
                'ok': False,
                'error': 'WebSockets are not supported on Vercel Serverless/Fluid Compute.',
            },
            status='503 Service Unavailable',
        )

    # Ensure Odoo's ProxyFix upgrades scheme to HTTPS behind Vercel's edge proxy
    if environ.get('HTTP_X_FORWARDED_PROTO') and not environ.get('HTTP_X_FORWARDED_HOST'):
        if environ.get('HTTP_HOST'):
            environ['HTTP_X_FORWARDED_HOST'] = environ['HTTP_HOST']

    bootstrap_odoo()

    cache_tier = _classify_cdn_cache(path, environ.get('QUERY_STRING', ''))
    wrapped_sr = _wrap_cdn_start_response(start_response, cache_tier)

    import odoo.http as odoo_http  # noqa: PLC0415
    return odoo_http.root(environ, wrapped_sr)


# Vercel detects either `app` or `application` as the WSGI callable
app = application

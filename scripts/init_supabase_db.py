#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Initialize or upgrade an Odoo 19 Community Edition database hosted on Supabase PostgreSQL.

Usage examples:
  # Fresh initialization with base + web + vercel_supabase
  DATABASE_URL="postgresql://postgres.<ref>:<pass>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require" \
    python scripts/init_supabase_db.py --admin-password "StrongPassword123!"

  # Install additional Odoo apps (e.g. CRM, Sales, Contacts)
  python scripts/init_supabase_db.py --modules crm,sale_management,contacts

  # Upgrade installed modules after pulling updates
  python scripts/init_supabase_db.py --upgrade base,vercel_supabase
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_dotenv_if_present() -> None:
    env_file = PROJECT_ROOT / '.env'
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    _load_dotenv_if_present()

    parser = argparse.ArgumentParser(
        description='Initialize or update Odoo 19 schema inside Supabase PostgreSQL.'
    )
    parser.add_argument(
        '--modules',
        default=os.environ.get(
            'ODOO_DEFAULT_MODULES',
            'base,web,contacts,sale_management,account,vercel_supabase',
        ),
        help='Comma-separated list of modules to install (default: base,web,contacts,sale_management,account,vercel_supabase).',
    )
    parser.add_argument(
        '--upgrade',
        default='',
        help='Comma-separated list of modules to upgrade instead of installing.',
    )
    parser.add_argument(
        '--lang',
        default=os.environ.get('ODOO_DEFAULT_LANG', 'en_US'),
        help='Default language code (default: en_US).',
    )
    parser.add_argument(
        '--admin-login',
        default=os.environ.get('ODOO_DEFAULT_ADMIN_LOGIN', 'admin'),
        help='Admin login username/email (default: admin).',
    )
    parser.add_argument(
        '--admin-password',
        default=os.environ.get('ODOO_DEFAULT_ADMIN_PASSWORD', ''),
        help='Admin password to set on base.user_admin.',
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Load Odoo demonstration data (off by default).',
    )
    args = parser.parse_args()

    from api.index import _BOOT_CONFIG_META, bootstrap_odoo  # noqa: PLC0415

    t0 = time.monotonic()
    print("Bootstrapping Odoo 19 configuration for Supabase...")
    bootstrap_odoo()

    import odoo  # noqa: PLC0415
    import odoo.api as api  # noqa: PLC0415
    from odoo.modules.registry import Registry  # noqa: PLC0415

    db_name = _BOOT_CONFIG_META['db_name']
    if _BOOT_CONFIG_META.get('is_transaction_pooler'):
        print(
            "WARNING: Port 6543 (Supabase Transaction Pooler) detected!\n"
            "Switch to Supabase Session Pooler (port 5432) or initialization may fail.",
            file=sys.stderr,
        )

    upgrade_mods = tuple(m.strip() for m in args.upgrade.split(',') if m.strip())
    install_mods = ()
    if not upgrade_mods:
        mods = [m.strip() for m in args.modules.split(',') if m.strip()]
        if 'vercel_supabase' not in mods:
            mods.append('vercel_supabase')
        install_mods = tuple(mods)

    registry = Registry.new(
        db_name,
        update_module=True,
        install_modules=install_mods,
        upgrade_modules=upgrade_mods,
        new_db_demo=args.demo,
    )

    with registry.cursor() as cr:
        if args.lang and args.lang != 'en_US':
            import odoo.tools.translate as odoo_translate  # noqa: PLC0415
            odoo_translate.load_language(cr, args.lang)
        env = api.Environment(cr, api.SUPERUSER_ID, {})
        if args.admin_password:
            admin_user = env.ref('base.user_admin', raise_if_not_found=False)
            if admin_user:
                admin_user.write({
                    'login': args.admin_login,
                    'password': args.admin_password,
                    'lang': args.lang,
                })
                print(f"Updated admin user '{args.admin_login}'.")

        from odoo.addons.vercel_supabase.models.ir_attachment import _get_filestore_mode  # noqa: PLC0415
        fs_mode = _get_filestore_mode()
        if fs_mode:
            env['ir.config_parameter'].sudo().set_param('ir_attachment.location', fs_mode)
            print(f"Set ir_attachment.location = '{fs_mode}'.")

        proforma_group = env.ref('sale.group_proforma_sales', raise_if_not_found=False)
        user_group = env.ref('base.group_user', raise_if_not_found=False)
        if proforma_group and user_group and proforma_group not in user_group.implied_ids:
            user_group.sudo().write({'implied_ids': [(4, proforma_group.id)]})
            print("Enabled Pro-Forma Invoices (sale.group_proforma_sales).")

        cr.commit()

    elapsed = time.monotonic() - t0
    print(f"Done! Supabase database '{db_name}' is ready ({elapsed:.1f}s).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

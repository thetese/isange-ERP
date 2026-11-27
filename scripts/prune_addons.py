#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Optional Vercel Bundle Size Optimizer for Odoo 19 Community Edition.

Full Odoo 19 Community Edition includes 400+ addons and dozens of translation
files (.po), which exceeds Vercel's standard 500 MB Python function limit.

You have two deployment choices:
  1. Keep ALL Odoo addons and set `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` in Vercel
     (enables up to 5 GB bundle size on Fluid Compute).
  2. OR run this script to keep only the addons you need (plus all recursive
     dependencies declared in each addon's `__manifest__.py`) and strip unused
     language translations / tests so the bundle stays well under 500 MB.

Examples:
  # Preview what would be kept/removed for CRM + Sales + Invoicing (dry-run)
  python scripts/prune_addons.py --keep crm,sale_management,account,contacts --langs en --dry-run

  # Apply pruning (removes unselected addons from ./addons and non-selected .po files)
  python scripts/prune_addons.py --keep crm,sale_management,account,contacts --langs en --apply
"""
from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADDONS_DIR = PROJECT_ROOT / 'addons'
CORE_ADDONS_DIR = PROJECT_ROOT / 'odoo' / 'addons'
CUSTOM_ADDONS_DIR = PROJECT_ROOT / 'custom_addons'

ALWAYS_KEEP = {
    'base',
    'web',
    'web_tour',
    'html_editor',
    'base_setup',
    'bus',
    'mail',
    'isange_property_fleet',
    'zxs_entp_theme',
    'vercel_supabase',
}

PRESETS = {
    'isange-erp': [
        # Core, auth, portal & currently installed Supabase modules
        'base',
        'web',
        'web_tour',
        'web_unsplash',
        'html_editor',
        'http_routing',
        'base_setup',
        'base_vat',
        'base_import',
        'base_import_module',
        'base_install_request',
        'api_doc',
        'rpc',
        'bus',
        'mail',
        'mail_bot',
        'mail_bot_hr',
        'digest',
        'onboarding',
        'iap',
        'iap_mail',
        'sms',
        'snailmail',
        'snailmail_account',
        'google_gmail',
        'google_address_autocomplete',
        'microsoft_outlook',
        'partner_autocomplete',
        'phone_validation',
        'privacy_lookup',
        'portal',
        'auth_signup',
        'auth_passkey',
        'auth_passkey_portal',
        'auth_totp',
        'auth_totp_mail',
        'auth_totp_portal',
        'utm',
        # Business apps + Rwanda localization + safe auto_install bridges
        'contacts',
        'analytic',
        'product',
        'uom',
        'barcodes',
        'barcodes_gs1_nomenclature',
        'iot_base',
        'payment',
        'account',
        'account_add_gln',
        'account_edi_ubl_cii',
        'account_fleet',
        'account_payment',
        'l10n_rw',
        'sales_team',
        'sale',
        'sale_edi_ubl',
        'sale_expense',
        'sale_management',
        'sale_pdf_quote_builder',
        'sale_purchase',
        'sale_purchase_stock',
        'sale_sms',
        'sale_stock',
        'purchase',
        'purchase_edi_ubl_bis3',
        'purchase_stock',
        'stock',
        'stock_account',
        'stock_maintenance',
        'stock_sms',
        'point_of_sale',
        'pos_hr',
        'pos_online_payment',
        'pos_sale',
        'resource',
        'resource_mail',
        'hr',
        'hr_expense',
        'hr_fleet',
        'hr_homeworking',
        'hr_maintenance',
        'hr_org_chart',
        'hr_skills',
        'web_hierarchy',
        'fleet',
        'maintenance',
        # Custom Isange ERP & Vercel/Supabase adapter
        'isange_property_fleet',
        'zxs_entp_theme',
        'vercel_supabase',
    ],
    'sales-invoicing': [
        'base',
        'web',
        'contacts',
        'sale_management',
        'sale_pdf_quote_builder',
        'account',
        'portal',
        'vercel_supabase',
    ],
    'minimal': [
        'base',
        'web',
        'contacts',
        'vercel_supabase',
    ],
}


def _read_depends(addon_dir: Path) -> list[str]:
    manifest = addon_dir / '__manifest__.py'
    if not manifest.is_file():
        return []
    try:
        data = ast.literal_eval(manifest.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return list(data.get('depends', []))
    except Exception:
        pass
    return []


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob('*'):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _resolve_closure(roots: set[str], addon_map: dict[str, Path]) -> set[str]:
    keep = set()
    stack = list(roots)
    while stack:
        mod = stack.pop()
        if mod in keep:
            continue
        keep.add(mod)
        addon_dir = addon_map.get(mod)
        if addon_dir:
            for dep in _read_depends(addon_dir):
                if dep not in keep:
                    stack.append(dep)
    return keep


def main() -> int:
    parser = argparse.ArgumentParser(description='Prune unused Odoo 19 addons & translations for Vercel.')
    parser.add_argument(
        '--preset',
        choices=sorted(PRESETS.keys()),
        default='',
        help='Use a curated preset (e.g. `isange-erp` for full Isange Hardware ERP, or `sales-invoicing`).',
    )
    parser.add_argument(
        '--keep',
        default='base,web,contacts,sale_management,purchase,stock,account,point_of_sale,fleet,maintenance,hr_expense,isange_property_fleet,zxs_entp_theme',
        help='Comma-separated root addons to keep (dependencies are resolved automatically).',
    )
    parser.add_argument(
        '--l10n',
        default='',
        help='Country chart-of-accounts modules to keep (e.g. `all`, `l10n_rw`, or `l10n_rw,l10n_ke`).',
    )
    parser.add_argument(
        '--langs',
        default='en',
        help='Comma-separated language prefixes to keep in i18n/*.po (e.g. en,fr,es). Use "all" to keep all.',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Actually delete unselected addons and .po files (without this flag, runs in dry-run mode).',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Explicitly run in dry-run mode (default when --apply is omitted).',
    )
    args = parser.parse_args()
    do_apply = args.apply and not args.dry_run

    addon_map: dict[str, Path] = {}
    for root_dir in (CORE_ADDONS_DIR, ADDONS_DIR, CUSTOM_ADDONS_DIR):
        if not root_dir.is_dir():
            continue
        for entry in root_dir.iterdir():
            if entry.is_dir() and (entry / '__manifest__.py').is_file():
                addon_map[entry.name] = entry

    if args.preset:
        requested = set(PRESETS[args.preset]) | ALWAYS_KEEP
    else:
        requested = {m.strip() for m in args.keep.split(',') if m.strip()} | ALWAYS_KEEP

    if args.l10n:
        if args.l10n.strip().lower() == 'all':
            requested |= {name for name in addon_map if name.startswith('l10n_')}
        else:
            requested |= {m.strip() for m in args.l10n.split(',') if m.strip()}
    missing = [m for m in sorted(requested) if m not in addon_map]
    if missing:
        print(f"Note: requested modules not found on disk: {', '.join(missing)}")

    keep_set = _resolve_closure(requested, addon_map)

    removable_addons = [
        entry for entry in sorted(ADDONS_DIR.iterdir())
        if entry.is_dir() and (entry / '__manifest__.py').is_file() and entry.name not in keep_set
    ]

    keep_langs = None
    if args.langs.strip().lower() != 'all':
        keep_langs = {lang.strip().lower() for lang in args.langs.split(',') if lang.strip()}

    freed_addons_bytes = sum(_dir_size_bytes(d) for d in removable_addons)
    kept_addons_count = len([m for m in keep_set if (ADDONS_DIR / m).is_dir()])

    po_to_remove: list[Path] = []
    if keep_langs is not None:
        for base_dir in (CORE_ADDONS_DIR, ADDONS_DIR):
            if not base_dir.is_dir():
                continue
            for po_file in base_dir.rglob('*.po'):
                # Skip addons already scheduled for full deletion
                if base_dir == ADDONS_DIR and po_file.relative_to(ADDONS_DIR).parts[0] not in keep_set:
                    continue
                stem = po_file.stem.lower()
                if not any(stem == lang or stem.startswith(f"{lang}_") for lang in keep_langs):
                    po_to_remove.append(po_file)

    freed_po_bytes = sum(f.stat().st_size for f in po_to_remove if f.exists())
    total_freed_mb = (freed_addons_bytes + freed_po_bytes) / (1024 * 1024)

    print(f"Kept addons (with recursive deps): {kept_addons_count} in ./addons (+ core/custom)")
    print(f"Removable addons in ./addons     : {len(removable_addons)} ({freed_addons_bytes / 1048576:.1f} MB)")
    print(f"Removable i18n .po files         : {len(po_to_remove)} ({freed_po_bytes / 1048576:.1f} MB)")
    print(f"Total estimated savings          : {total_freed_mb:.1f} MB")

    if not do_apply:
        print("\nDry-run only. Re-run with `--apply` to delete unselected addons and translations.")
        return 0

    for addon_dir in removable_addons:
        shutil.rmtree(addon_dir, ignore_errors=True)
    for po_file in po_to_remove:
        try:
            po_file.unlink()
        except OSError:
            pass

    print(f"\nPruned {len(removable_addons)} addons and {len(po_to_remove)} .po files (~{total_freed_mb:.1f} MB freed).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

# -*- coding: utf-8 -*-
{
    'name': 'Vercel + Supabase Runtime Adapter',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'Adapts Odoo 19 Community (Sales, Pro-Forma & Invoicing) for Vercel Fluid Compute + Supabase',
    'description': """
Vercel + Supabase Runtime Adapter for Odoo 19 Community Edition
================================================================

Replaces stateful local-disk Odoo components so Odoo 19 can run on
Vercel Python Functions (Fluid Compute) backed by Supabase:

- PostgreSQL-backed HTTP session store (stored in `odoo_vercel_sessions` in Supabase)
- Supabase Storage (or PostgreSQL `db`) attachment & asset filestore with `/tmp` read-through cache
- PostgreSQL-backed filestore garbage collection checklist
- Serverless PDF report engine for Quotations, Pro-Forma Invoices & Customer Invoices
  (built-in pure-Python `xhtml2pdf` + optional Gotenberg/Browserless/Cloudflare Chromium API)
    """,
    'author': 'Custom Vercel/Supabase Conversion',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [],
    'installable': True,
    'auto_install': True,
    'application': False,
}

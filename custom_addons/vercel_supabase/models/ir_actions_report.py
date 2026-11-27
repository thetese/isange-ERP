# -*- coding: utf-8 -*-
"""
Serverless PDF Rendering Engine for Odoo 19 on Vercel (Sales, Pro-Forma & Invoices).

Vercel's Python runtime does not ship with `wkhtmltopdf` or Qt shared libraries.
This model overrides `ir.actions.report.get_wkhtmltopdf_state` and
`ir.actions.report._run_wkhtmltopdf` with a three-tier rendering strategy:

  1. External Chromium / Gotenberg / Browserless / Cloudflare PDF Service
     (when `ODOO_PDF_SERVICE_URL` is configured) — 100% pixel-accurate Bootstrap 5
     Odoo layouts with zero HTTP callbacks back to Vercel (all CSS/images inlined).
  2. Built-in Pure-Python `xhtml2pdf` + ReportLab fallback — works out of the box
     on Vercel with zero external services, resolving Odoo company logos, barcodes,
     and attachments in-process and normalizing Bootstrap invoice tables.
  3. Native `wkhtmltopdf` — automatically used when available on traditional Linux/Docker
     hosts (unless overridden via `ODOO_PDF_ENGINE`).
"""
from __future__ import annotations

import base64
import contextlib
import io
import logging
import os
import re
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

import lxml.etree
import lxml.html
import requests

from odoo import api, models, _
from odoo.exceptions import UserError
from odoo.http import root

_logger = logging.getLogger(__name__)

# Standard Odoo paperformat dimensions in mm (width, height)
_PAPER_SIZES_MM = {
    'A0': (841, 1189),
    'A1': (594, 841),
    'A2': (420, 594),
    'A3': (297, 420),
    'A4': (210, 297),
    'A5': (148, 210),
    'A6': (105, 148),
    'B5': (176, 250),
    'Letter': (215.9, 279.4),
    'Legal': (215.9, 355.6),
    'Executive': (184.1, 266.7),
}

# Clean CSS2.1 stylesheet tailored for Odoo 19 Sales Quotations, Pro-Forma Invoices,
# and Customer Invoices rendered through xhtml2pdf (ReportLab).
_XHTML2PDF_BASE_CSS = """
* {
    box-sizing: border-box;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.38;
    color: #212529;
    margin: 0;
    padding: 0;
}
h1, h2, h3, h4, h5 {
    color: #1f2937;
    margin: 6pt 0 4pt 0;
    font-weight: bold;
}
h2 { font-size: 15pt; }
h3 { font-size: 12.5pt; }
h4 { font-size: 10.5pt; }
p { margin: 2pt 0 4pt 0; }
small, .small { font-size: 8pt; color: #555555; }
strong, b, .fw-bold, .font-weight-bold { font-weight: bold; }
.text-muted { color: #6c757d; }
.text-end, .text-right { text-align: right; }
.text-center { text-align: center; }
.text-start, .text-left { text-align: left; }
.text-nowrap { white-space: nowrap; }
.text-uppercase { text-transform: uppercase; }
.d-none, .o_hide, .oe_hide { display: none; }
.mb-0 { margin-bottom: 0; }
.mb-1 { margin-bottom: 3pt; }
.mb-2 { margin-bottom: 6pt; }
.mb-3 { margin-bottom: 9pt; }
.mb-4 { margin-bottom: 12pt; }
.mt-1 { margin-top: 3pt; }
.mt-2 { margin-top: 6pt; }
.mt-3 { margin-top: 9pt; }
.mt-4 { margin-top: 12pt; }
.pt-2 { padding-top: 6pt; }
.pb-2 { padding-bottom: 6pt; }
.border-top { border-top: 0.75pt solid #dee2e6; }
.border-bottom { border-bottom: 0.75pt solid #dee2e6; }
.border-black { border-color: #212529; }

/* Company logo & header */
img { max-width: 100%; }
ul.list-unstyled { list-style-type: none; margin: 0; padding: 0; }
.o_company_logo, .o_company_logo_small, .o_company_logo_big {
    max-height: 46pt;
    width: auto;
}
.header {
    font-size: 8.5pt;
    color: #374151;
    border-bottom: 0.75pt solid #d1d5db;
    padding-bottom: 4pt;
    margin-bottom: 8pt;
}
.footer {
    font-size: 8pt;
    color: #6b7280;
    border-top: 0.75pt solid #d1d5db;
    padding-top: 4pt;
    text-align: center;
}
.isange_bank_details table {
    margin-top: 0;
    margin-bottom: 0;
}

/* Tables (Quotations, Pro-Forma Invoices, Customer Invoices) */
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 6pt;
    margin-bottom: 8pt;
}
table.o_main_table th,
table.table th {
    background-color: #f3f4f6;
    color: #111827;
    font-weight: bold;
    border-bottom: 1pt solid #9ca3af;
    border-top: 0.5pt solid #d1d5db;
    padding: 5pt 4pt;
    text-align: left;
    vertical-align: bottom;
}
table.o_main_table td,
table.table td {
    border-bottom: 0.5pt solid #e5e7eb;
    padding: 4.5pt 4pt;
    vertical-align: top;
}
table.o_main_table tr.o_line_section td,
tr.is-section td {
    background-color: #f9fafb;
    font-weight: bold;
}
table.o_main_table tr.o_line_note td {
    font-style: italic;
    color: #4b5563;
}
table.o_total_table {
    width: 52%;
    margin-left: 48%;
    margin-top: 4pt;
}
table.o_total_table td {
    border-bottom: 0.5pt solid #e5e7eb;
    padding: 4pt 5pt;
}
table.o_total_table tr.o_total td {
    border-top: 1.2pt solid #111827;
    border-bottom: 1.2pt solid #111827;
    font-weight: bold;
    font-size: 10.5pt;
    background-color: #f9fafb;
}
table.o_layout_grid {
    width: 100%;
    border-collapse: collapse;
    margin: 4pt 0 8pt 0;
}
table.o_layout_grid td {
    border: none;
    padding: 2pt 6pt 2pt 0;
    vertical-align: top;
}
"""


def _has_xhtml2pdf() -> bool:
    try:
        import xhtml2pdf.pisa  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def get_active_pdf_engine(native_wkhtml_state: str = 'install') -> str:
    """Return active PDF engine name: 'service', 'xhtml2pdf', 'wkhtmltopdf', or 'unavailable'."""
    mode = (os.environ.get('ODOO_PDF_ENGINE') or 'auto').strip().lower()
    service_url = (os.environ.get('ODOO_PDF_SERVICE_URL') or '').strip()

    if mode == 'wkhtmltopdf':
        return 'wkhtmltopdf' if native_wkhtml_state == 'ok' else 'unavailable'
    if mode == 'service':
        return 'service' if service_url else 'unavailable'
    if mode == 'xhtml2pdf':
        return 'xhtml2pdf' if _has_xhtml2pdf() else 'unavailable'

    # Auto mode priority:
    # 1) External Chromium/Gotenberg/Browserless service if explicitly configured
    if service_url:
        return 'service'
    # 2) Native wkhtmltopdf when installed (e.g. Docker/VPS)
    if native_wkhtml_state == 'ok':
        return 'wkhtmltopdf'
    # 3) Built-in pure-Python xhtml2pdf (default on Vercel)
    if _has_xhtml2pdf():
        return 'xhtml2pdf'
    return 'unavailable'


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    @api.model
    def get_wkhtmltopdf_state(self):
        native_state = super().get_wkhtmltopdf_state()
        engine = get_active_pdf_engine(native_state)
        if engine in ('service', 'xhtml2pdf'):
            return 'ok'
        return native_state

    # ------------------------------------------------------------------
    # Paperformat helpers
    # ------------------------------------------------------------------
    @api.model
    def _extract_page_geometry(self, paperformat_id, landscape=False, specific_paperformat_args=None):
        """Return dict with width_mm, height_mm, margin_top/bottom/left/right_mm, landscape."""
        fmt_name = (paperformat_id.format if paperformat_id else None) or 'A4'
        if fmt_name == 'custom' and paperformat_id and paperformat_id.page_width and paperformat_id.page_height:
            width_mm = float(paperformat_id.page_width)
            height_mm = float(paperformat_id.page_height)
        else:
            width_mm, height_mm = _PAPER_SIZES_MM.get(fmt_name, (210.0, 297.0))

        is_landscape = bool(landscape) or (
            paperformat_id and getattr(paperformat_id, 'orientation', '') == 'Landscape'
        )
        if is_landscape and width_mm < height_mm:
            width_mm, height_mm = height_mm, width_mm

        spec = specific_paperformat_args or {}

        def _mm(spec_key, pf_attr, default_val):
            raw = spec.get(spec_key)
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
            if paperformat_id and getattr(paperformat_id, pf_attr, None) is not None:
                try:
                    return float(getattr(paperformat_id, pf_attr))
                except (TypeError, ValueError):
                    pass
            return float(default_val)

        return {
            'width_mm': width_mm,
            'height_mm': height_mm,
            'margin_top_mm': max(_mm('data-report-margin-top', 'margin_top', 22), 10.0),
            'margin_bottom_mm': max(_mm('data-report-margin-bottom', 'margin_bottom', 18), 10.0),
            'margin_left_mm': max(_mm('data-report-margin-left', 'margin_left', 10), 5.0),
            'margin_right_mm': max(_mm('data-report-margin-right', 'margin_right', 10), 5.0),
            'landscape': is_landscape,
        }

    # ------------------------------------------------------------------
    # In-process Odoo Asset & Image Resolution (zero HTTP loopback)
    # ------------------------------------------------------------------
    @api.model
    def _resolve_odoo_asset(self, uri: str) -> tuple[bytes | None, str | None]:
        """Resolve an Odoo internal URL (/web/image, /web/assets, /<addon>/static, /report/barcode)
        directly in-process so serverless PDF rendering never deadlocks on HTTP loopback."""
        if not uri or uri.startswith('data:'):
            return None, None

        parsed = urlsplit(uri.strip())
        path = unquote(parsed.path or '')
        query = parse_qs(parsed.query)

        # Only resolve relative paths or URLs pointing to our own Odoo base URL
        if parsed.scheme in ('http', 'https'):
            base_url = (self._get_report_url() or '').rstrip('/')
            base_host = urlsplit(base_url).hostname if base_url else None
            if parsed.hostname not in (base_host, 'localhost', '127.0.0.1'):
                return None, None

        if not path.startswith('/'):
            return None, None

        # 1) /web/assets/... or /web/content/... (compiled CSS/JS/attachments)
        if path.startswith('/web/assets/') or path.startswith('/web/content'):
            Att = self.env['ir.attachment'].sudo()
            att = Att.search([('url', '=', path)], limit=1)
            if not att:
                m = re.match(r'^/web/content/(\d+)', path)
                if m:
                    att = Att.browse(int(m.group(1))).exists()
            if att and att.raw:
                return att.raw, att.mimetype or 'application/octet-stream'

        # 2) /web/image/... (company logos, product images, signatures)
        if path.startswith('/web/image'):
            raw, mimetype = self._resolve_web_image(path, query)
            if raw:
                return raw, mimetype

        # 3) /report/barcode/<type>/<value>
        if path.startswith('/report/barcode'):
            raw = self._resolve_barcode(path, query)
            if raw:
                return raw, 'image/png'

        # 4) /<module>/static/...
        static_path = root.get_static_file(path)
        if static_path and os.path.isfile(static_path):
            try:
                with open(static_path, 'rb') as fp:
                    data = fp.read()
                ext = os.path.splitext(static_path)[1].lower()
                mime_map = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.gif': 'image/gif',
                    '.svg': 'image/svg+xml',
                    '.webp': 'image/webp',
                    '.css': 'text/css',
                    '.woff': 'font/woff',
                    '.woff2': 'font/woff2',
                    '.ttf': 'font/ttf',
                }
                return data, mime_map.get(ext, 'application/octet-stream')
            except OSError:
                pass

        return None, None

    @api.model
    def _resolve_web_image(self, path: str, query: dict) -> tuple[bytes | None, str | None]:
        """Resolve /web/image/<model>/<id>/<field> or /web/image/<att_id> in-process."""
        try:
            # Form 1: /web/image/<model>/<id>/<field>
            m = re.match(r'^/web/image/([a-zA-Z0-9_.]+)/(\d+)/([a-zA-Z0-9_]+)', path)
            if m:
                model_name, rec_id, field_name = m.group(1), int(m.group(2)), m.group(3)
            elif query.get('model') and query.get('id'):
                model_name = query['model'][0]
                rec_id = int(query['id'][0])
                field_name = (query.get('field') or ['raw'])[0]
            else:
                # Form 2: /web/image/<att_id> or /web/image/<att_id>-<unique>
                m2 = re.match(r'^/web/image/(\d+)', path)
                if not m2:
                    return None, None
                att = self.env['ir.attachment'].sudo().browse(int(m2.group(1))).exists()
                if att and att.raw:
                    return att.raw, att.mimetype or 'image/png'
                return None, None

            if model_name not in self.env:
                return None, None
            record = self.env[model_name].sudo().browse(rec_id).exists()
            if not record:
                return None, None

            if model_name == 'ir.attachment' and field_name in ('raw', 'datas', 'db_datas'):
                return record.raw, record.mimetype or 'image/png'

            val = record[field_name]
            if not val:
                return None, None
            if isinstance(val, str):
                val = val.encode('ascii')
            try:
                raw = base64.b64decode(val)
            except Exception:
                raw = val

            from odoo.tools.mimetypes import guess_mimetype  # noqa: PLC0415
            mimetype = guess_mimetype(raw, default='image/png')
            return raw, mimetype
        except Exception:
            _logger.debug("In-process /web/image resolution failed for %s", path, exc_info=True)
            return None, None

    @api.model
    def _resolve_barcode(self, path: str, query: dict) -> bytes | None:
        try:
            m = re.match(r'^/report/barcode/([^/]+)/(.+)$', path)
            if m:
                btype, value = m.group(1), m.group(2)
            else:
                btype = (query.get('barcode_type') or query.get('type') or ['Code128'])[0]
                value = (query.get('value') or [''])[0]
            if not value:
                return None
            width = int((query.get('width') or [600])[0])
            height = int((query.get('height') or [100])[0])
            humanreadable = bool(int((query.get('humanreadable') or [0])[0]))
            return self.barcode(btype, value, width=width, height=height, humanreadable=humanreadable)
        except Exception:
            _logger.debug("In-process barcode resolution failed for %s", path, exc_info=True)
            return None

    @api.model
    def _inline_html_assets(self, html_str: str, inline_stylesheets: bool = True) -> str:
        """Inline Odoo images (and optionally CSS stylesheets) directly into HTML."""
        if not html_str:
            return ''
        try:
            doc = lxml.html.fromstring(html_str)
        except Exception:
            return html_str

        # Inline <img src="..."> (converting WebP -> PNG for ReportLab compatibility)
        for img in doc.xpath('.//img[@src]'):
            src = img.get('src') or ''
            if src.startswith('data:'):
                if src.startswith('data:image/webp;base64,'):
                    try:
                        from PIL import Image  # noqa: PLC0415
                        raw_webp = base64.b64decode(src.split(',', 1)[1])
                        buf = io.BytesIO()
                        Image.open(io.BytesIO(raw_webp)).convert('RGBA').save(buf, format='PNG')
                        img.set('src', 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii'))
                    except Exception:
                        pass
                continue
            raw, mimetype = self._resolve_odoo_asset(src)
            if raw:
                if (mimetype or '').lower() == 'image/webp':
                    try:
                        from PIL import Image  # noqa: PLC0415
                        buf = io.BytesIO()
                        Image.open(io.BytesIO(raw)).convert('RGBA').save(buf, format='PNG')
                        raw = buf.getvalue()
                        mimetype = 'image/png'
                    except Exception:
                        pass
                b64 = base64.b64encode(raw).decode('ascii')
                img.set('src', f"data:{mimetype or 'image/png'};base64,{b64}")

        # Handle <link rel="stylesheet" href="...">
        for link in doc.xpath('.//link[@rel="stylesheet" and @href]'):
            href = link.get('href') or ''
            if inline_stylesheets:
                raw, _mime = self._resolve_odoo_asset(href)
                if raw:
                    style_el = lxml.html.Element('style')
                    style_el.set('type', 'text/css')
                    style_el.text = raw.decode('utf-8', errors='replace')
                    parent = link.getparent()
                    if parent is not None:
                        parent.replace(link, style_el)
                    continue
            else:
                parent = link.getparent()
                if parent is not None:
                    parent.remove(link)

        # In xhtml2pdf mode (inline_stylesheets=False), remove embedded CSS3 <style> blocks
        # and sanitize inline style declarations using CSS3 var()/calc() unsupported by CSS2.1
        if not inline_stylesheets:
            for st in doc.xpath('.//style'):
                parent = st.getparent()
                if parent is not None:
                    parent.remove(st)
            for el in doc.xpath('.//*[@style]'):
                style_val = el.get('style') or ''
                if 'var(' in style_val or 'calc(' in style_val or 'clamp(' in style_val:
                    kept_decls = [
                        decl for decl in style_val.split(';')
                        if decl.strip() and 'var(' not in decl and 'calc(' not in decl and 'clamp(' not in decl
                    ]
                    if kept_decls:
                        el.set('style', '; '.join(kept_decls))
                    else:
                        el.attrib.pop('style', None)

        # Strip <script> tags (not needed in static PDF rendering)
        for script in doc.xpath('.//script'):
            parent = script.getparent()
            if parent is not None:
                parent.remove(script)

        return lxml.html.tostring(doc, encoding='unicode')

    # ------------------------------------------------------------------
    # Engine 1: External Chromium / Gotenberg / Browserless / Cloudflare
    # ------------------------------------------------------------------
    @api.model
    def _render_pdf_via_service(
        self, bodies, paperformat_id, header=None, footer=None,
        landscape=False, specific_paperformat_args=None,
    ) -> bytes:
        service_url = (os.environ.get('ODOO_PDF_SERVICE_URL') or '').strip()
        service_type = (os.environ.get('ODOO_PDF_SERVICE_TYPE') or 'gotenberg').strip().lower()
        token = (os.environ.get('ODOO_PDF_SERVICE_TOKEN') or '').strip()
        geom = self._extract_page_geometry(paperformat_id, landscape, specific_paperformat_args)

        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'

        pdf_chunks: list[bytes] = []
        inlined_header = self._inline_html_assets(header, inline_stylesheets=True) if header else None
        inlined_footer = self._inline_html_assets(footer, inline_stylesheets=True) if footer else None

        for body in bodies:
            inlined_body = self._inline_html_assets(body, inline_stylesheets=True)
            if service_type == 'gotenberg':
                # Gotenberg Chromium HTML endpoint: POST /forms/chromium/convert/html
                endpoint = service_url
                if '/forms/chromium/' not in endpoint:
                    endpoint = endpoint.rstrip('/') + '/forms/chromium/convert/html'
                files = {
                    'files': ('index.html', inlined_body.encode('utf-8'), 'text/html'),
                }
                form_data = {
                    'paperWidth': f"{geom['width_mm'] / 25.4:.3f}",
                    'paperHeight': f"{geom['height_mm'] / 25.4:.3f}",
                    'marginTop': f"{geom['margin_top_mm'] / 25.4:.3f}",
                    'marginBottom': f"{geom['margin_bottom_mm'] / 25.4:.3f}",
                    'marginLeft': f"{geom['margin_left_mm'] / 25.4:.3f}",
                    'marginRight': f"{geom['margin_right_mm'] / 25.4:.3f}",
                    'printBackground': 'true',
                    'landscape': 'true' if geom['landscape'] else 'false',
                }
                if inlined_header:
                    files['header'] = ('header.html', inlined_header.encode('utf-8'), 'text/html')
                if inlined_footer:
                    files['footer'] = ('footer.html', inlined_footer.encode('utf-8'), 'text/html')
                resp = requests.post(endpoint, headers=headers, files=files, data=form_data, timeout=90)
            elif service_type in ('browserless', 'cloudflare'):
                # Browserless /pdf or Cloudflare Browser Rendering /pdf JSON API
                payload = {
                    'html': inlined_body,
                    'options': {
                        'printBackground': True,
                        'landscape': geom['landscape'],
                        'width': f"{geom['width_mm']}mm",
                        'height': f"{geom['height_mm']}mm",
                        'margin': {
                            'top': f"{geom['margin_top_mm']}mm",
                            'bottom': f"{geom['margin_bottom_mm']}mm",
                            'left': f"{geom['margin_left_mm']}mm",
                            'right': f"{geom['margin_right_mm']}mm",
                        },
                    },
                }
                if inlined_header or inlined_footer:
                    payload['options']['displayHeaderFooter'] = True
                    payload['options']['headerTemplate'] = inlined_header or '<span></span>'
                    payload['options']['footerTemplate'] = inlined_footer or '<span></span>'
                req_headers = {**headers, 'Content-Type': 'application/json'}
                resp = requests.post(service_url, headers=req_headers, json=payload, timeout=90)
            else:
                # Generic HTTP service expecting raw HTML POST and returning PDF bytes
                req_headers = {**headers, 'Content-Type': 'text/html; charset=utf-8'}
                resp = requests.post(service_url, headers=req_headers, data=inlined_body.encode('utf-8'), timeout=90)

            if resp.status_code != 200 or not resp.content.startswith(b'%PDF'):
                raise UserError(_(
                    "External PDF service (%s) failed with HTTP %s: %s",
                    service_type, resp.status_code, resp.text[:400],
                ))
            pdf_chunks.append(resp.content)

        return self._merge_pdf_chunks(pdf_chunks)

    # ------------------------------------------------------------------
    # Engine 2: Built-in Pure-Python xhtml2pdf (ReportLab)
    # ------------------------------------------------------------------
    @api.model
    def _extract_body_fragment(self, html_str: str) -> str:
        """Extract inner HTML inside <body> and convert simple Bootstrap .row columns
        into xhtml2pdf-compatible tables so address blocks & headers sit side-by-side."""
        if not html_str:
            return ''
        cleaned = self._inline_html_assets(html_str, inline_stylesheets=False)
        try:
            doc = lxml.html.fromstring(cleaned)
        except Exception:
            return cleaned

        # Convert simple Bootstrap `.row` containers with direct `.col*` children
        # into borderless 2+ column layout tables (xhtml2pdf ignores CSS flexbox/grid).
        for row in doc.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " row ")]'):
            col_children = [
                child for child in row
                if isinstance(child.tag, str)
                and any(
                    cls == 'col' or cls.startswith('col-')
                    for cls in (child.get('class') or '').split()
                )
            ]
            if len(col_children) < 2 or len(col_children) != len([c for c in row if isinstance(c.tag, str)]):
                continue
            tbl = lxml.html.Element('table')
            tbl.set('class', 'o_layout_grid')
            tr = lxml.etree.SubElement(tbl, 'tr')
            width_pct = round(100.0 / len(col_children), 2)
            for col in col_children:
                td = lxml.etree.SubElement(tr, 'td')
                td_class = col.get('class') or ''
                td.set('class', td_class)
                # Respect Bootstrap col-N / col-md-N widths when explicitly set
                m_col = re.search(r'\bcol-(?:sm-|md-|lg-)?(\d{1,2})\b', td_class)
                col_pct = round(int(m_col.group(1)) * 100.0 / 12.0, 2) if m_col else width_pct
                td.set('style', f'width: {col_pct}%; vertical-align: top;')
                if col.text:
                    td.text = col.text
                for sub in list(col):
                    td.append(sub)
            parent = row.getparent()
            if parent is not None:
                parent.replace(row, tbl)

        # Convert 2-column `.d-flex` header containers (e.g. web.external_layout_striped
        # company logo + company_address) into side-by-side layout tables for xhtml2pdf.
        for flex in doc.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " d-flex ")]'):
            div_children = [c for c in flex if isinstance(c.tag, str)]
            if len(div_children) != 2 or not all(c.tag == 'div' for c in div_children):
                continue
            tbl = lxml.html.Element('table')
            tbl.set('class', 'o_layout_grid')
            tr = lxml.etree.SubElement(tbl, 'tr')
            for idx, col in enumerate(div_children):
                td = lxml.etree.SubElement(tr, 'td')
                td.set('class', col.get('class') or '')
                pad_left = '12pt' if idx == 1 else '0'
                td.set('style', f'width: 50%; vertical-align: top; text-align: left; padding-left: {pad_left};')
                if col.text:
                    td.text = col.text
                for sub in list(col):
                    td.append(sub)
            parent = flex.getparent()
            if parent is not None:
                parent.replace(flex, tbl)

        body_el = doc.find('.//body')
        if body_el is not None:
            parts = []
            if body_el.text:
                parts.append(body_el.text)
            for child in body_el:
                parts.append(lxml.html.tostring(child, encoding='unicode'))
            return ''.join(parts)
        return lxml.html.tostring(doc, encoding='unicode')

    @api.model
    def _render_pdf_via_xhtml2pdf(
        self, bodies, paperformat_id, header=None, footer=None,
        landscape=False, specific_paperformat_args=None,
    ) -> bytes:
        from xhtml2pdf import pisa  # noqa: PLC0415

        geom = self._extract_page_geometry(paperformat_id, landscape, specific_paperformat_args)
        header_html = self._extract_body_fragment(header) if header else ''
        footer_html = self._extract_body_fragment(footer) if footer else ''

        # Replace Odoo's JS-driven page-number spans with xhtml2pdf native counters
        if footer_html:
            footer_html = re.sub(
                r'<span\s+class="page"[^>]*>.*?</span>',
                '<pdf:pagenumber/>',
                footer_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            footer_html = re.sub(
                r'<span\s+class="topage"[^>]*>.*?</span>',
                '<pdf:pagecount/>',
                footer_html,
                flags=re.IGNORECASE | re.DOTALL,
            )

        margin_left = geom['margin_left_mm']
        margin_right = geom['margin_right_mm']
        margin_top = geom['margin_top_mm']
        margin_bottom = geom['margin_bottom_mm']
        header_h = max(margin_top - 4.0, 14.0) if header_html else 0.0
        footer_h = max(margin_bottom - 4.0, 12.0) if footer_html else 0.0

        frames_css = [
            f"""
            @frame content_frame {{
                left: {margin_left}mm;
                right: {margin_right}mm;
                top: {margin_top}mm;
                bottom: {margin_bottom}mm;
            }}"""
        ]
        if header_html:
            frames_css.append(
                f"""
                @frame header_frame {{
                    -pdf-frame-content: odoo_header_content;
                    left: {margin_left}mm;
                    right: {margin_right}mm;
                    top: 4mm;
                    height: {header_h}mm;
                }}"""
            )
        if footer_html:
            frames_css.append(
                f"""
                @frame footer_frame {{
                    -pdf-frame-content: odoo_footer_content;
                    left: {margin_left}mm;
                    right: {margin_right}mm;
                    bottom: 4mm;
                    height: {footer_h}mm;
                }}"""
            )

        page_css = f"""
        @page {{
            size: {geom['width_mm']}mm {geom['height_mm']}mm;
            {''.join(frames_css)}
        }}
        {_XHTML2PDF_BASE_CSS}
        """

        temp_files: list[str] = []

        def _link_callback(uri, rel):
            raw, _mime = self._resolve_odoo_asset(uri)
            if raw:
                fd, path = tempfile.mkstemp(prefix='odoo_pdf_asset_')
                with os.fdopen(fd, 'wb') as fp:
                    fp.write(raw)
                temp_files.append(path)
                return path
            return uri

        pdf_chunks: list[bytes] = []
        try:
            for body in bodies:
                body_html = self._extract_body_fragment(body)
                full_html = (
                    "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
                    f"<style>{page_css}</style></head><body>"
                    + (f"<div id='odoo_header_content'>{header_html}</div>" if header_html else "")
                    + (f"<div id='odoo_footer_content'>{footer_html}</div>" if footer_html else "")
                    + f"<div id='odoo_body_content'>{body_html}</div>"
                    + "</body></html>"
                )
                out_buf = io.BytesIO()
                status = pisa.CreatePDF(
                    src=io.BytesIO(full_html.encode('utf-8')),
                    dest=out_buf,
                    encoding='utf-8',
                    link_callback=_link_callback,
                )
                if status.err:
                    _logger.warning("xhtml2pdf reported %s warning/error(s) rendering report.", status.err)
                pdf_bytes = out_buf.getvalue()
                if not pdf_bytes.startswith(b'%PDF'):
                    raise UserError(_("Serverless PDF rendering failed to produce a valid PDF document."))
                pdf_chunks.append(pdf_bytes)
        finally:
            for tf in temp_files:
                with contextlib.suppress(OSError):
                    os.unlink(tf)

        return self._merge_pdf_chunks(pdf_chunks)

    @api.model
    def _merge_pdf_chunks(self, pdf_chunks: list[bytes]) -> bytes:
        if not pdf_chunks:
            return b''
        if len(pdf_chunks) == 1:
            return pdf_chunks[0]
        try:
            from pypdf import PdfReader, PdfWriter  # noqa: PLC0415
        except ImportError:
            from PyPDF2 import PdfReader, PdfWriter  # noqa: PLC0415

        writer = PdfWriter()
        for chunk in pdf_chunks:
            reader = PdfReader(io.BytesIO(chunk))
            for page in reader.pages:
                writer.add_page(page)
        merged = io.BytesIO()
        writer.write(merged)
        return merged.getvalue()

    # ------------------------------------------------------------------
    # Main Odoo 19 Override
    # ------------------------------------------------------------------
    @api.model
    def _run_wkhtmltopdf(
        self,
        bodies,
        report_ref=False,
        header=None,
        footer=None,
        landscape=False,
        specific_paperformat_args=None,
        set_viewport_size=False,
    ):
        native_state = super().get_wkhtmltopdf_state()
        engine = get_active_pdf_engine(native_state)

        if engine == 'wkhtmltopdf':
            return super()._run_wkhtmltopdf(
                bodies,
                report_ref=report_ref,
                header=header,
                footer=footer,
                landscape=landscape,
                specific_paperformat_args=specific_paperformat_args,
                set_viewport_size=set_viewport_size,
            )

        paperformat_id = self._get_report(report_ref).get_paperformat() if report_ref else self.get_paperformat()

        if engine == 'service':
            _logger.info("Rendering Odoo PDF via external PDF service (%s doc(s))", len(bodies))
            return self._render_pdf_via_service(
                bodies,
                paperformat_id=paperformat_id,
                header=header,
                footer=footer,
                landscape=landscape,
                specific_paperformat_args=specific_paperformat_args,
            )

        if engine == 'xhtml2pdf':
            _logger.info("Rendering Odoo PDF via built-in pure-Python xhtml2pdf (%s doc(s))", len(bodies))
            return self._render_pdf_via_xhtml2pdf(
                bodies,
                paperformat_id=paperformat_id,
                header=header,
                footer=footer,
                landscape=landscape,
                specific_paperformat_args=specific_paperformat_args,
            )

        raise UserError(_(
            "No PDF engine is available. Install `xhtml2pdf` (included in requirements.txt), "
            "configure `ODOO_PDF_SERVICE_URL`, or install `wkhtmltopdf`."
        ))

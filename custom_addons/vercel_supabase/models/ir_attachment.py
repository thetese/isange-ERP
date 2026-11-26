# -*- coding: utf-8 -*-
import contextlib
import logging
import os
import re
import tempfile
import threading
from urllib.parse import quote

import psycopg2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from odoo import api, models, sql_db, _
from odoo.exceptions import UserError
from odoo.http import Stream
from odoo.tools import split_every

_logger = logging.getLogger(__name__)

_HTTP_SESSION_LOCK = threading.Lock()
_HTTP_SESSION = None
_GC_TABLE_READY = False
_GC_TABLE_LOCK = threading.Lock()


def _get_http_session() -> requests.Session:
    """Return a shared, pooled requests.Session for Supabase Storage calls."""
    global _HTTP_SESSION
    if _HTTP_SESSION is None:
        with _HTTP_SESSION_LOCK:
            if _HTTP_SESSION is None:
                session = requests.Session()
                retry = Retry(
                    total=3,
                    backoff_factor=0.3,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset(['GET', 'POST', 'PUT', 'DELETE', 'HEAD']),
                )
                adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=retry)
                session.mount('https://', adapter)
                session.mount('http://', adapter)
                _HTTP_SESSION = session
    return _HTTP_SESSION


def _get_filestore_mode() -> str:
    """Resolve effective filestore mode from environment.

    Modes:
      - ``supabase``: store attachments/assets in Supabase Storage with /tmp read-through cache
      - ``db``: store attachments/assets directly in Supabase PostgreSQL (ir_attachment.db_datas)
      - ``file``: standard Odoo local filesystem storage
    """
    explicit = (os.environ.get('ODOO_FILESTORE_MODE') or '').strip().lower()
    if explicit in ('supabase', 'db', 'file'):
        return explicit

    has_supabase_storage = bool(
        (os.environ.get('SUPABASE_URL') or os.environ.get('NEXT_PUBLIC_SUPABASE_URL') or '').strip()
        and (
            os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
            or os.environ.get('SUPABASE_STORAGE_KEY')
            or ''
        ).strip()
    )
    if has_supabase_storage:
        return 'supabase'
    if os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV'):
        return 'db'
    return ''


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    # ------------------------------------------------------------------
    # Storage backend selection
    # ------------------------------------------------------------------
    @api.model
    def _storage(self):
        mode = _get_filestore_mode()
        if mode:
            return mode
        return super()._storage()

    @api.model
    def _get_storage_domain(self):
        storage = self._storage()
        if storage == 'supabase':
            return [('db_datas', '!=', False)]
        return super()._get_storage_domain()

    # ------------------------------------------------------------------
    # Supabase Storage helpers
    # ------------------------------------------------------------------
    @api.model
    def _supabase_storage_config(self):
        base_url = (
            os.environ.get('SUPABASE_URL')
            or os.environ.get('NEXT_PUBLIC_SUPABASE_URL')
            or ''
        ).strip().rstrip('/')
        api_key = (
            os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
            or os.environ.get('SUPABASE_STORAGE_KEY')
            or ''
        ).strip()
        bucket = (os.environ.get('SUPABASE_STORAGE_BUCKET') or 'odoo-filestore').strip()
        prefix = (os.environ.get('SUPABASE_STORAGE_PREFIX') or self.env.cr.dbname or 'postgres').strip('/')
        if not base_url or not api_key:
            raise UserError(_(
                "Supabase Storage is enabled (ODOO_FILESTORE_MODE=supabase) but "
                "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing."
            ))
        return base_url, api_key, bucket, prefix

    @api.model
    def _supabase_object_key(self, fname: str) -> str:
        clean = re.sub(r'[.:]', '', fname or '').strip('/\\')
        _base_url, _api_key, _bucket, prefix = self._supabase_storage_config()
        return f"{prefix}/{clean}" if prefix else clean

    @api.model
    def _supabase_headers(self, api_key: str, extra: dict | None = None) -> dict:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'apikey': api_key,
        }
        if extra:
            headers.update(extra)
        return headers

    @api.model
    def _ensure_gc_table(self, dbname: str | None = None):
        global _GC_TABLE_READY
        if _GC_TABLE_READY:
            return
        with _GC_TABLE_LOCK:
            if _GC_TABLE_READY:
                return
            db = sql_db.db_connect(dbname or self.env.cr.dbname)
            with db.cursor() as gc_cr:
                gc_cr.execute("""
                    CREATE TABLE IF NOT EXISTS public.odoo_vercel_filestore_gc (
                        fname TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            _GC_TABLE_READY = True

    @api.model
    def _cache_write_local(self, full_path: str, bin_value: bytes) -> None:
        """Best-effort atomic write to ephemeral /tmp read-through cache."""
        try:
            dirname = os.path.dirname(full_path)
            os.makedirs(dirname, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix='.tmp_att_')
            try:
                with os.fdopen(fd, 'wb') as fp:
                    fp.write(bin_value)
                os.replace(tmp_path, full_path)
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            _logger.debug("Skipping ephemeral /tmp cache write for %s", full_path, exc_info=True)

    # ------------------------------------------------------------------
    # Core ir.attachment filestore overrides (Odoo 19 API)
    # ------------------------------------------------------------------
    @api.model
    def _get_path(self, bin_data, sha):
        if self._storage() != 'supabase':
            return super()._get_path(bin_data, sha)
        fname = sha[:2] + '/' + sha
        full_path = self._full_path(fname)
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
        return fname, full_path

    @api.model
    def _file_write(self, bin_value, checksum):
        if self._storage() != 'supabase':
            return super()._file_write(bin_value, checksum)

        fname, full_path = self._get_path(bin_value, checksum)
        base_url, api_key, bucket, _prefix = self._supabase_storage_config()
        obj_key = self._supabase_object_key(fname)
        encoded_key = quote(obj_key, safe='/')
        upload_url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}/{encoded_key}"

        # Mark in GC checklist before upload so aborted transactions are cleaned later
        self._mark_for_gc(fname)

        resp = _get_http_session().post(
            upload_url,
            headers=self._supabase_headers(api_key, {
                'Content-Type': 'application/octet-stream',
                'x-upsert': 'true',
                'cache-control': '31536000',
            }),
            data=bin_value,
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            _logger.error(
                "Supabase Storage upload failed for %s (%s): %s",
                obj_key, resp.status_code, resp.text[:500],
            )
            raise UserError(_("Failed to store attachment in Supabase Storage (HTTP %s).", resp.status_code))

        # Populate warm-instance /tmp cache
        self._cache_write_local(full_path, bin_value)
        return fname

    @api.model
    def _file_read(self, fname, size=None):
        if self._storage() != 'supabase':
            return super()._file_read(fname, size=size)

        full_path = self._full_path(fname)
        # 1) Fast path: warm-instance /tmp read-through cache
        try:
            with open(full_path, 'rb') as fp:
                return fp.read(size)
        except OSError:
            pass

        # 2) Cache miss: download from Supabase Storage
        base_url, api_key, bucket, _prefix = self._supabase_storage_config()
        obj_key = self._supabase_object_key(fname)
        encoded_key = quote(obj_key, safe='/')
        download_url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}/{encoded_key}"

        try:
            resp = _get_http_session().get(
                download_url,
                headers=self._supabase_headers(api_key),
                timeout=60,
            )
            if resp.status_code == 200:
                content = resp.content
                self._cache_write_local(full_path, content)
                return content[:size] if size is not None else content
            if resp.status_code in (400, 404):
                _logger.info("Attachment %s not found in Supabase Storage (HTTP %s)", obj_key, resp.status_code)
                return b''
            _logger.warning(
                "Supabase Storage download error for %s (HTTP %s): %s",
                obj_key, resp.status_code, resp.text[:300],
            )
        except requests.RequestException:
            _logger.warning("Supabase Storage request exception reading %s", obj_key, exc_info=True)
        return b''

    @api.model
    def _file_delete(self, fname):
        if self._storage() != 'supabase':
            return super()._file_delete(fname)
        self._mark_for_gc(fname)
        # Remove local /tmp cache copy if present
        with contextlib.suppress(OSError):
            os.unlink(self._full_path(fname))

    def _mark_for_gc(self, fname):
        if self._storage() != 'supabase':
            return super()._mark_for_gc(fname)
        clean = re.sub(r'[.:]', '', fname or '').strip('/\\')
        if not clean:
            return
        try:
            dbname = self.env.cr.dbname
            self._ensure_gc_table(dbname)
            # Use an independent cursor so the GC marker persists even if the
            # main transaction rolls back after uploading to Supabase Storage.
            with sql_db.db_connect(dbname).cursor() as gc_cr:
                gc_cr.execute(
                    """
                    INSERT INTO public.odoo_vercel_filestore_gc (fname)
                    VALUES (%s)
                    ON CONFLICT (fname) DO NOTHING
                    """,
                    (clean,),
                )
        except Exception:
            _logger.debug("Could not record %s in Supabase GC table", clean, exc_info=True)

    @api.autovacuum
    def _gc_file_store(self):
        storage = self._storage()
        if storage != 'supabase':
            return super()._gc_file_store()

        cr = self.env.cr
        self._ensure_gc_table()
        cr.commit()

        cr.execute("SET LOCAL lock_timeout TO '10s'")
        try:
            cr.execute("LOCK ir_attachment IN SHARE MODE")
        except psycopg2.errors.LockNotAvailable:
            cr.rollback()
            return False

        self._gc_supabase_storage_unsafe()
        cr.commit()
        return True

    def _gc_supabase_storage_unsafe(self):
        cr = self.env.cr
        cr.execute("SELECT fname FROM public.odoo_vercel_filestore_gc")
        checklist = [row[0] for row in cr.fetchall()]
        if not checklist:
            return

        base_url, api_key, bucket, _prefix = self._supabase_storage_config()
        delete_url = f"{base_url}/storage/v1/object/{quote(bucket, safe='')}"
        removed = 0

        for names in split_every(min(self.env.cr.IN_MAX, 100), checklist):
            cr.execute(
                "SELECT store_fname FROM ir_attachment WHERE store_fname IN %s",
                [tuple(names)],
            )
            whitelist = {row[0] for row in cr.fetchall()}
            to_delete = [fname for fname in names if fname not in whitelist]

            if to_delete:
                object_keys = [self._supabase_object_key(fname) for fname in to_delete]
                try:
                    resp = _get_http_session().delete(
                        delete_url,
                        headers=self._supabase_headers(api_key, {'Content-Type': 'application/json'}),
                        json={'prefixes': object_keys},
                        timeout=60,
                    )
                    if resp.status_code in (200, 204):
                        removed += len(to_delete)
                    else:
                        _logger.warning(
                            "Supabase Storage GC delete failed (HTTP %s): %s",
                            resp.status_code, resp.text[:300],
                        )
                except requests.RequestException:
                    _logger.warning("Supabase Storage GC request failed", exc_info=True)

                for fname in to_delete:
                    with contextlib.suppress(OSError):
                        os.unlink(self._full_path(fname))

            cr.execute(
                "DELETE FROM public.odoo_vercel_filestore_gc WHERE fname IN %s",
                [tuple(names)],
            )

        _logger.info("Supabase filestore GC: %d checked, %d removed", len(checklist), removed)

    # ------------------------------------------------------------------
    # HTTP streaming override (critical for Odoo 19 on ephemeral /tmp)
    # ------------------------------------------------------------------
    def _to_http_stream(self):
        self.ensure_one()
        if self._storage() == 'supabase' and self.store_fname:
            data = self._file_read(self.store_fname)
            return Stream(
                type='data',
                data=data,
                mimetype=self.mimetype,
                download_name=self.name,
                etag=self.checksum,
                last_modified=self.write_date,
                size=len(data),
                public=self.public,
            )
        return super()._to_http_stream()

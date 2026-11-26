# -*- coding: utf-8 -*-
import base64
import contextlib
import functools
import json
import logging
import os
import re
import threading
import time
from hashlib import sha512

from odoo import sql_db
from odoo.service import security
from odoo.tools import config
from odoo.tools._vendor import sessions

_logger = logging.getLogger(__name__)

# Match Odoo 19's odoo/http.py constants exactly
SESSION_LIFETIME = 60 * 60 * 24 * 7  # 7 days
SESSION_DELETION_TIMER = 120
STORED_SESSION_BYTES = 42

_base64_urlsafe_re = re.compile(r'^[A-Za-z0-9_-]{84}$')
_session_identifier_re = re.compile(r'^[A-Za-z0-9_-]{%s}$' % STORED_SESSION_BYTES)

_TABLE_READY = False
_TABLE_LOCK = threading.Lock()


def _resolve_session_dbname() -> str:
    """Determine which PostgreSQL database stores HTTP sessions."""
    env_db = (
        os.environ.get('ODOO_SESSION_DB')
        or os.environ.get('SUPABASE_DB_NAME')
        or os.environ.get('POSTGRES_DATABASE')
        or os.environ.get('PGDATABASE')
    )
    if env_db:
        return env_db.strip()
    db_names = config.get('db_name')
    if isinstance(db_names, (list, tuple)) and db_names:
        return db_names[0]
    if isinstance(db_names, str) and db_names:
        return db_names.split(',')[0].strip()
    return 'postgres'


class SupabasePostgresSessionStore(sessions.SessionStore):
    """PostgreSQL-backed Odoo 19 SessionStore for stateless Vercel functions.

    Implements the full Odoo 19 ``FilesystemSessionStore`` interface
    (including soft/hard session rotation, identifier tracking for
    ``res.device.log``, and session vacuuming) backed by the
    ``public.odoo_vercel_sessions`` table in Supabase PostgreSQL.
    """

    def __init__(self, session_class=None, renew_missing=True, dbname=None):
        super().__init__(session_class=session_class)
        self.renew_missing = renew_missing
        self._explicit_dbname = dbname
        # Compatibility attribute for third-party code expecting `.path`
        self.path = config.session_dir

    @property
    def _dbname(self) -> str:
        return self._explicit_dbname or _resolve_session_dbname()

    def _ensure_table(self, cr) -> None:
        global _TABLE_READY
        if _TABLE_READY:
            return
        with _TABLE_LOCK:
            if _TABLE_READY:
                return
            cr.execute("""
                CREATE TABLE IF NOT EXISTS public.odoo_vercel_sessions (
                    sid VARCHAR(128) PRIMARY KEY,
                    identifier VARCHAR(64) NOT NULL,
                    data JSONB NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
            """)
            cr.execute("""
                CREATE INDEX IF NOT EXISTS idx_odoo_vercel_sessions_identifier
                    ON public.odoo_vercel_sessions (identifier)
            """)
            cr.execute("""
                CREATE INDEX IF NOT EXISTS idx_odoo_vercel_sessions_updated_at
                    ON public.odoo_vercel_sessions (updated_at)
            """)
            _TABLE_READY = True

    @contextlib.contextmanager
    def _cursor(self):
        db = sql_db.db_connect(self._dbname)
        with db.cursor() as cr:
            self._ensure_table(cr)
            yield cr

    def generate_key(self, salt=None):
        """Generate an 84-char URL-safe base64 session ID matching Odoo 19."""
        key = str(time.time()).encode() + os.urandom(64)
        hash_key = sha512(key).digest()[:-1]  # 63 bytes -> 84 base64 chars, no padding
        return base64.urlsafe_b64encode(hash_key).decode('utf-8')

    def is_valid_key(self, key):
        return bool(key) and _base64_urlsafe_re.match(key) is not None

    def save(self, session):
        if not self.is_valid_key(session.sid):
            raise ValueError(f'Invalid session id {session.sid!r}')
        payload = json.dumps(dict(session))
        identifier = session.sid[:STORED_SESSION_BYTES]
        now = time.time()
        with self._cursor() as cr:
            cr.execute(
                """
                INSERT INTO public.odoo_vercel_sessions (sid, identifier, data, updated_at)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT (sid) DO UPDATE
                SET identifier = EXCLUDED.identifier,
                    data = EXCLUDED.data,
                    updated_at = EXCLUDED.updated_at
                """,
                (session.sid, identifier, payload, now),
            )

    def delete(self, session):
        if not session or not session.sid:
            return
        try:
            with self._cursor() as cr:
                cr.execute(
                    "DELETE FROM public.odoo_vercel_sessions WHERE sid = %s",
                    (session.sid,),
                )
        except Exception:
            _logger.debug("Failed to delete session %s", session.sid[:10], exc_info=True)

    def get(self, sid):
        if not self.is_valid_key(sid):
            return self.new()
        try:
            with self._cursor() as cr:
                cr.execute(
                    "SELECT data FROM public.odoo_vercel_sessions WHERE sid = %s",
                    (sid,),
                )
                row = cr.fetchone()
        except Exception:
            _logger.warning("Could not load session from Supabase PostgreSQL.", exc_info=True)
            row = None

        if row is None:
            if self.renew_missing:
                return self.new()
            return self.session_class({}, sid, False)

        raw = row[0]
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                _logger.debug("Corrupt JSON in session %s; using empty session.", sid[:10], exc_info=True)
                data = {}
        elif isinstance(raw, dict):
            data = raw
        else:
            data = {}
        return self.session_class(data, sid, False)

    def delete_old_sessions(self, session):
        if 'gc_previous_sessions' in session:
            if session.get('create_time', 0) + SESSION_DELETION_TIMER < time.time():
                self.delete_from_identifiers([session.sid[:STORED_SESSION_BYTES]])
                del session['gc_previous_sessions']
                self.save(session)

    def rotate(self, session, env, soft=False):
        if soft:
            static = session.sid[:STORED_SESSION_BYTES]
            recent_session = self.get(session.sid)
            if 'next_sid' in recent_session:
                session.sid = recent_session['next_sid']
                return
            next_sid = static + self.generate_key()[STORED_SESSION_BYTES:]
            session['next_sid'] = next_sid
            session['deletion_time'] = time.time() + SESSION_DELETION_TIMER
            self.save(session)
            session['gc_previous_sessions'] = True
            session.sid = next_sid
            session.pop('deletion_time', None)
            session.pop('next_sid', None)
        else:
            self.delete(session)
            session.sid = self.generate_key()
        if session.uid:
            assert env, "saving this session requires an environment"
            session.session_token = security.compute_session_token(session, env)
        session.should_rotate = False
        session['create_time'] = time.time()
        self.save(session)

    def vacuum(self, max_lifetime=SESSION_LIFETIME):
        threshold = time.time() - max_lifetime
        try:
            with self._cursor() as cr:
                cr.execute(
                    "DELETE FROM public.odoo_vercel_sessions WHERE updated_at < %s",
                    (threshold,),
                )
                return cr.rowcount
        except Exception:
            _logger.warning("Failed to vacuum Supabase sessions", exc_info=True)
            return 0

    def get_missing_session_identifiers(self, identifiers):
        identifiers = set(identifiers)
        if not identifiers:
            return set()
        try:
            with self._cursor() as cr:
                cr.execute(
                    "SELECT DISTINCT identifier FROM public.odoo_vercel_sessions WHERE identifier = ANY(%s)",
                    (list(identifiers),),
                )
                present = {row[0] for row in cr.fetchall()}
            return identifiers - present
        except Exception:
            _logger.warning("Could not query missing session identifiers", exc_info=True)
            return set()

    def delete_from_identifiers(self, identifiers):
        valid = []
        for identifier in identifiers:
            if not _session_identifier_re.match(identifier):
                raise ValueError(
                    "Identifier format incorrect, did you pass in a string instead of a list?"
                )
            valid.append(identifier)
        if not valid:
            return
        try:
            with self._cursor() as cr:
                cr.execute(
                    "DELETE FROM public.odoo_vercel_sessions WHERE identifier = ANY(%s)",
                    (valid,),
                )
        except Exception:
            _logger.warning("Failed to delete sessions by identifier", exc_info=True)

    def list(self):
        with self._cursor() as cr:
            cr.execute("SELECT sid FROM public.odoo_vercel_sessions")
            return [row[0] for row in cr.fetchall()]


def should_enable_postgres_sessions() -> bool:
    mode = (os.environ.get('ODOO_SESSION_STORE') or '').strip().lower()
    if mode in ('filesystem', 'file', 'disk', 'false', '0', 'off'):
        return False
    if mode in ('postgres', 'postgresql', 'supabase', 'db', 'true', '1', 'on'):
        return True
    return bool(
        os.environ.get('VERCEL')
        or os.environ.get('VERCEL_ENV')
        or os.environ.get('DATABASE_URL')
        or os.environ.get('SUPABASE_DB_URL')
        or os.environ.get('SUPABASE_DB_HOST')
        or os.environ.get('POSTGRES_URL')
        or os.environ.get('POSTGRES_URL_NON_POOLING')
    )


def install_supabase_session_store() -> None:
    """Replace Odoo 19's FilesystemSessionStore with SupabasePostgresSessionStore."""
    if not should_enable_postgres_sessions():
        _logger.info("vercel_supabase: keeping default Odoo filesystem session store.")
        return

    import odoo.http as odoo_http  # noqa: PLC0415

    if getattr(odoo_http.Application, '_supabase_session_patched', False):
        return

    @functools.cached_property
    def _supabase_session_store(self):
        _logger.info(
            "vercel_supabase: using SupabasePostgresSessionStore on database '%s'",
            _resolve_session_dbname(),
        )
        return SupabasePostgresSessionStore(
            session_class=odoo_http.Session,
            renew_missing=True,
        )

    _supabase_session_store.__set_name__(odoo_http.Application, 'session_store')
    odoo_http.Application.session_store = _supabase_session_store
    odoo_http.Application._supabase_session_patched = True

    # Clear cached_property on existing `odoo.http.root` singleton if already accessed
    odoo_http.root.__dict__.pop('session_store', None)
    _logger.info("vercel_supabase: patched odoo.http.Application.session_store -> Supabase PostgreSQL")

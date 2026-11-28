# -*- coding: utf-8 -*-
"""Automatic content-based asset cache busting for ``custom_addons`` on Vercel.

In Odoo 19 production mode (``is_debug_assets = False``), ``WebAsset.last_modified``
returns ``-1`` for all filesystem assets, so editing JS/XML/SCSS inside
``custom_addons`` and deploying to Vercel keeps the exact same 7-char bundle
hash and continues serving stale ``ir.attachment`` + CDN bundles.

By returning a cached SHA-1 content token for files under ``custom_addons``,
only bundles that actually include modified custom files get a new immutable
URL hash on deploy, while core Odoo bundles remain untouched and CDN-cached.
"""
import hashlib
import logging
import os

_logger = logging.getLogger(__name__)
_INSTALLED = False
_HASH_CACHE: dict[str, int] = {}


def _custom_file_token(filename: str) -> int | None:
    if not filename:
        return None
    norm = filename.replace('\\', '/')
    if '/custom_addons/' not in norm:
        return None
    cached = _HASH_CACHE.get(norm)
    if cached is not None:
        return cached
    try:
        with open(filename, 'rb') as fh:
            digest = hashlib.sha1(fh.read()).hexdigest()[:12]
        token = int(digest, 16)
        _HASH_CACHE[norm] = token
        return token
    except OSError:
        return None


def install_custom_asset_versioning() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from odoo.addons.base.models import assetsbundle  # noqa: PLC0415
    except Exception:
        _logger.debug("Could not import odoo.addons.base.models.assetsbundle", exc_info=True)
        return

    orig_prop = assetsbundle.WebAsset.last_modified
    orig_fget = orig_prop.fget

    def _patched_last_modified(self):
        if self._last_modified is None and self._filename and not self.bundle.is_debug_assets:
            token = _custom_file_token(self._filename)
            if token is not None:
                self._last_modified = token
                return self._last_modified
        return orig_fget(self)

    assetsbundle.WebAsset.last_modified = property(_patched_last_modified)
    _INSTALLED = True
    _logger.info("Activated content-based asset versioning for custom_addons.")

# -*- coding: utf-8 -*-
from . import models
from .asset_versioning import install_custom_asset_versioning
from .session_store import install_supabase_session_store

# Activate PostgreSQL-backed sessions and auto-versioning for custom_addons
# immediately when loaded as a server-wide module.
install_supabase_session_store()
install_custom_asset_versioning()

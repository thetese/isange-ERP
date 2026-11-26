# -*- coding: utf-8 -*-
from . import models
from .session_store import install_supabase_session_store

# Activate PostgreSQL-backed sessions immediately when loaded as a server-wide module
install_supabase_session_store()

"""
Local development settings — the default for `manage.py` and everything
else that doesn't explicitly opt into `config.settings.production` (see
`manage.py`, `config/wsgi.py`, `config/asgi.py`).
"""

from .base import *  # noqa: F401,F403

DEBUG = True

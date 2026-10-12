"""
WSGI config for asknget project.

It exposes the WSGI callable as a module-level variable named ``application``
(and as ``app``, the name Vercel's Python runtime looks for).

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

# The directory holding manage.py. Vercel imports this module from outside it, so
# `asknget.settings` and the apps are only importable once it is on sys.path.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'asknget.settings')

application = get_wsgi_application()
app = application

"""
WSGI config for asknget project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path
from django.core.wsgi import get_wsgi_application

# BASE_DIR points to the directory containing manage.py (one level up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent

# Add BASE_DIR to sys.path so Python can resolve 'asknget.settings'
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'asknget.settings')

application = get_wsgi_application()

# Alias for Vercel's Python runtime
app = application

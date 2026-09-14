import os
import sys
from pathlib import Path

# Add project root to sys.path so src and web packages resolve on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from web.dashboard_api import app

# Vercel looks for the WSGI application object named 'app'

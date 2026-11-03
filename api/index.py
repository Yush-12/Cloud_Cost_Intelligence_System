"""Vercel entry point. Hosted AWS mode requires durable storage and a stable secret."""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from flask import Flask


def create_vercel_app(config):
    database_url = config.get('DATABASE_URL') or config.get('POSTGRES_URL')
    session_secret = config.get('COSTINTEL_SESSION_SECRET')
    if not database_url or not session_secret or len(session_secret) < 32:
        app = Flask(__name__)

        @app.get('/')
        @app.get('/login')
        def setup_required():
            return ('Hosted AWS workspace setup is incomplete. Connect Neon Postgres and set '
                    'COSTINTEL_SESSION_SECRET in the Vercel project, then redeploy.', 503)

        return app

    from costintel.hosted_store import HostedSnapshotStore
    from costintel.hosted_sessions import HostedSessions
    from costintel.web import create_app

    store = HostedSnapshotStore(database_url)
    registry = HostedSessions(store, session_secret)
    return create_app(store, 'hosted', 'aws', '', 'ap-south-1', deployment='hosted',
                      session_registry=registry, session_secret=session_secret)


app = create_vercel_app(os.environ)

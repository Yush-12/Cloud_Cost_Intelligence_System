"""Encrypted temporary AWS credentials in durable hosted sessions."""
import base64
import hashlib
import json
import time

import boto3
from cryptography.fernet import Fernet


class HostedSessions:
    def __init__(self, store, secret):
        if len(secret) < 32:
            raise ValueError('COSTINTEL_SESSION_SECRET must contain at least 32 characters')
        self.store=store
        self.cipher=Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS hosted_sessions (
                sid TEXT PRIMARY KEY, payload BYTEA NOT NULL, expires_at DOUBLE PRECISION NOT NULL)''')

    def __setitem__(self, sid, scope):
        payload={k:v for k,v in scope.items() if k != 'sdk'}
        expires_at=time.time()+max(0,scope['expires']-time.monotonic())
        encrypted=self.cipher.encrypt(json.dumps(payload).encode())
        with self.store.connect() as db:
            db.execute('''INSERT INTO hosted_sessions VALUES (%s,%s,%s)
                ON CONFLICT(sid) DO UPDATE SET payload=EXCLUDED.payload,expires_at=EXCLUDED.expires_at''',
                (sid,encrypted,expires_at))

    def get(self, sid, default=None):
        if not sid:
            return default
        with self.store.connect() as db:
            row=db.execute('SELECT payload,expires_at FROM hosted_sessions WHERE sid=%s',(sid,)).fetchone()
        if not row or row[1] <= time.time():
            return default
        scope=json.loads(self.cipher.decrypt(bytes(row[0])).decode())
        scope['expires']=time.monotonic()+(row[1]-time.time())
        if scope.get('mode') == 'aws':
            scope['sdk']=boto3.Session(region_name=scope['region'],**scope['credentials'])
        return scope

    def __getitem__(self, sid):
        result=self.get(sid)
        if result is None:
            raise KeyError(sid)
        return result

    def __contains__(self, sid):
        return self.get(sid) is not None

    def pop(self, sid, default=None):
        value=self.get(sid,default)
        if sid:
            with self.store.connect() as db:
                db.execute('DELETE FROM hosted_sessions WHERE sid=%s',(sid,))
        return value

    def items(self):
        # Expiry is enforced on lookup; avoid scanning other users' sessions.
        return ()

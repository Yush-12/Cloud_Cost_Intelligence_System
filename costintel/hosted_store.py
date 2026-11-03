"""Durable, owner-scoped observations for the hosted read-only workspace."""
import uuid


class HostedSnapshotStore:
    def __init__(self, database_url):
        if not database_url:
            raise ValueError('DATABASE_URL is required for hosted AWS workspaces')
        self.database_url = database_url
        self.initialize()

    def connect(self):
        import psycopg
        return psycopg.connect(self.database_url, connect_timeout=5)

    def initialize(self):
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS hosted_snapshots (
                sequence BIGSERIAL PRIMARY KEY, snapshot_id TEXT UNIQUE NOT NULL,
                owner TEXT NOT NULL, mode TEXT NOT NULL, account_id TEXT NOT NULL,
                region TEXT NOT NULL, payload JSONB NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS hosted_snapshot_scope ON hosted_snapshots
                (owner, mode, account_id, region, sequence DESC)''')
            db.execute('''CREATE TABLE IF NOT EXISTS hosted_observations (
                owner TEXT NOT NULL, mode TEXT NOT NULL, account_id TEXT NOT NULL,
                region TEXT NOT NULL, resource_id TEXT NOT NULL, metric TEXT NOT NULL,
                statistic TEXT NOT NULL, period_seconds INTEGER NOT NULL,
                timestamp TEXT NOT NULL, unit TEXT NOT NULL, value DOUBLE PRECISION NOT NULL,
                PRIMARY KEY(owner,mode,account_id,region,resource_id,metric,statistic,period_seconds,timestamp))''')
            db.execute('''CREATE TABLE IF NOT EXISTS hosted_billing_attempts (
                owner TEXT NOT NULL, account_id TEXT NOT NULL, attempt_date TEXT NOT NULL,
                status TEXT NOT NULL, payload JSONB,
                PRIMARY KEY(owner,account_id,attempt_date))''')

    def save(self, owner, snapshot):
        if not owner or snapshot['mode'] != 'aws':
            raise ValueError('Hosted storage accepts scoped AWS snapshots only')
        from psycopg.types.json import Jsonb
        snapshot_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('''INSERT INTO hosted_snapshots
                (snapshot_id,owner,mode,account_id,region,payload) VALUES (%s,%s,%s,%s,%s,%s)''',
                (snapshot_id,owner,snapshot['mode'],snapshot['account_id'],snapshot['region'],Jsonb(snapshot)))
            for series in snapshot.get('telemetry',{}).get('series',[]):
                for point in series['points']:
                    db.execute('''INSERT INTO hosted_observations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(owner,mode,account_id,region,resource_id,metric,statistic,period_seconds,timestamp)
                        DO UPDATE SET value=EXCLUDED.value,unit=EXCLUDED.unit''',
                        (owner,snapshot['mode'],snapshot['account_id'],snapshot['region'],series['resource_id'],
                         series['metric'],series['statistic'],series['period_seconds'],point['timestamp'],
                         series['unit'],point['value']))
        return snapshot_id

    def latest(self, owner, mode, account_id, region):
        with self.connect() as db:
            row=db.execute('''SELECT payload FROM hosted_snapshots WHERE owner=%s AND mode=%s
                AND account_id=%s AND region=%s ORDER BY sequence DESC LIMIT 1''',
                (owner,mode,account_id,region)).fetchone()
        return row[0] if row else None

    def billing_claim(self, owner, account_id, attempt_date):
        with self.connect() as db:
            result = db.execute('''INSERT INTO hosted_billing_attempts
                (owner,account_id,attempt_date,status) VALUES (%s,%s,%s,'pending')
                ON CONFLICT(owner,account_id,attempt_date) DO NOTHING''',
                (owner,account_id,attempt_date))
        return result.rowcount == 1

    def billing_finish(self, owner, account_id, attempt_date, payload):
        from psycopg.types.json import Jsonb
        with self.connect() as db:
            db.execute('''UPDATE hosted_billing_attempts SET status='ready',payload=%s
                WHERE owner=%s AND account_id=%s AND attempt_date=%s AND status='pending' ''',
                (Jsonb(payload),owner,account_id,attempt_date))

    def billing_fail(self, owner, account_id, attempt_date):
        with self.connect() as db:
            db.execute('''UPDATE hosted_billing_attempts SET status='failed'
                WHERE owner=%s AND account_id=%s AND attempt_date=%s AND status='pending' ''',
                (owner,account_id,attempt_date))

    def billing_latest(self, owner, account_id):
        with self.connect() as db:
            row=db.execute('''SELECT attempt_date,status,payload FROM hosted_billing_attempts
                WHERE owner=%s AND account_id=%s ORDER BY attempt_date DESC LIMIT 1''',
                (owner,account_id)).fetchone()
        return {'attempt_date':row[0], 'status':row[1], 'payload':row[2]} if row else None

    def history(self, owner, mode, account_id, region, resource_id, metric, limit=288):
        if not 1 <= limit <= 2016:
            raise ValueError('History limit must be between 1 and 2016')
        with self.connect() as db:
            rows=db.execute('''SELECT timestamp,value,unit,statistic,period_seconds
                FROM hosted_observations WHERE owner=%s AND mode=%s AND account_id=%s AND region=%s
                AND resource_id=%s AND metric=%s ORDER BY timestamp DESC LIMIT %s''',
                (owner,mode,account_id,region,resource_id,metric,limit)).fetchall()
        return [dict(zip(('timestamp','value','unit','statistic','period_seconds'),row)) for row in reversed(rows)]

    def review_history(self, owner, mode, account_id, region):
        return []

    containment_history = review_history
    optimization_history = review_history

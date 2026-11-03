"""Durable snapshots scoped by owner, provider, account and region."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class SnapshotStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                mode TEXT NOT NULL, account_id TEXT NOT NULL, region TEXT NOT NULL,
                payload TEXT NOT NULL)""")
            db.execute("""CREATE INDEX IF NOT EXISTS snapshot_scope ON snapshots
                (owner, mode, account_id, region, sequence)""")
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                owner TEXT NOT NULL, mode TEXT NOT NULL, account_id TEXT NOT NULL,
                region TEXT NOT NULL, resource_id TEXT NOT NULL, metric TEXT NOT NULL,
                statistic TEXT NOT NULL, period_seconds INTEGER NOT NULL,
                timestamp TEXT NOT NULL, unit TEXT NOT NULL, value REAL NOT NULL,
                PRIMARY KEY (owner, mode, account_id, region, resource_id,
                             metric, statistic, period_seconds, timestamp))""")
            db.execute("""CREATE TABLE IF NOT EXISTS billing_attempts (
                owner TEXT NOT NULL, account_id TEXT NOT NULL, attempt_date TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT,
                PRIMARY KEY (owner, account_id, attempt_date))""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, owner, snapshot):
        if not owner or snapshot["mode"] not in {"demo", "aws"}:
            raise ValueError("A nonempty owner and explicit provider mode are required")
        snapshot_id = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("""INSERT INTO snapshots
                (snapshot_id, owner, mode, account_id, region, payload)
                VALUES (?, ?, ?, ?, ?, ?)""", (snapshot_id, owner, snapshot["mode"],
                snapshot["account_id"], snapshot["region"], json.dumps(snapshot)))
            for series in snapshot.get("telemetry", {}).get("series", []):
                for point in series["points"]:
                    db.execute("""INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT DO UPDATE SET value=excluded.value, unit=excluded.unit""",
                        (owner, snapshot["mode"], snapshot["account_id"], snapshot["region"],
                         series["resource_id"], series["metric"], series["statistic"],
                         series["period_seconds"], point["timestamp"], series["unit"], point["value"]))
        return snapshot_id

    def latest(self, owner, mode, account_id, region):
        with self.connect() as db:
            row = db.execute("""SELECT payload FROM snapshots WHERE
                owner=? AND mode=? AND account_id=? AND region=?
                ORDER BY sequence DESC LIMIT 1""", (owner, mode, account_id, region)).fetchone()
        return json.loads(row[0]) if row else None

    def billing_claim(self, owner, account_id, attempt_date):
        with self.connect() as db:
            result = db.execute("""INSERT OR IGNORE INTO billing_attempts
                (owner, account_id, attempt_date, status) VALUES (?, ?, ?, 'pending')""",
                (owner, account_id, attempt_date))
        return result.rowcount == 1

    def billing_finish(self, owner, account_id, attempt_date, payload):
        with self.connect() as db:
            db.execute("""UPDATE billing_attempts SET status='ready', payload=?
                WHERE owner=? AND account_id=? AND attempt_date=? AND status='pending'""",
                (json.dumps(payload), owner, account_id, attempt_date))

    def billing_fail(self, owner, account_id, attempt_date):
        with self.connect() as db:
            db.execute("""UPDATE billing_attempts SET status='failed'
                WHERE owner=? AND account_id=? AND attempt_date=? AND status='pending'""",
                (owner, account_id, attempt_date))

    def billing_latest(self, owner, account_id):
        with self.connect() as db:
            row = db.execute("""SELECT attempt_date, status, payload FROM billing_attempts
                WHERE owner=? AND account_id=? ORDER BY attempt_date DESC LIMIT 1""",
                (owner, account_id)).fetchone()
        return ({'attempt_date': row[0], 'status': row[1],
                 'payload': json.loads(row[2]) if row[2] else None} if row else None)

    def review_history(self, owner, mode, account_id, region):
        if owner != 'local' or mode != 'aws':
            return []
        with self.connect() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='review_actions'").fetchone():
                return []
            rows = db.execute('SELECT plan, status FROM review_actions ORDER BY rowid DESC').fetchall()
        results = []
        for payload, status in rows:
            plan = json.loads(payload)
            if plan['account'] == account_id and plan['region'] == region:
                results.append({**plan, 'status': status})
        return results

    def containment_history(self, owner, mode, account_id, region):
        if owner != 'local' or mode != 'aws' or region != 'ap-south-1':
            return []
        with self.connect() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='containment_actions'").fetchone():
                return []
            rows = db.execute('SELECT id, plan, status, created_at FROM containment_actions ORDER BY rowid DESC').fetchall()
        results = []
        for action_id, payload, status, created_at in rows:
            plan = json.loads(payload)
            if plan['account'] == account_id:
                results.append({**plan, 'id': action_id, 'status': status, 'created_at': created_at})
        return results

    def optimization_history(self, owner, mode, account_id, region):
        if owner != 'local' or mode != 'aws':
            return []
        with self.connect() as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_actions'").fetchone():
                return []
            rows = db.execute('SELECT id, plan, status, created_at FROM optimization_actions ORDER BY rowid DESC').fetchall()
        results = []
        for action_id, payload, status, created_at in rows:
            plan = json.loads(payload)
            if plan.get('account') == account_id and plan.get('region') == region:
                results.append({**plan, 'id': action_id, 'status': status, 'created_at': created_at})
        return results

    def history(self, owner, mode, account_id, region, resource_id, metric, limit=288):
        if not 1 <= limit <= 2016:
            raise ValueError("History limit must be between 1 and 2016")
        with self.connect() as db:
            rows = db.execute("""SELECT timestamp, value, unit, statistic, period_seconds
                FROM observations WHERE owner=? AND mode=? AND account_id=? AND region=?
                AND resource_id=? AND metric=? ORDER BY timestamp DESC LIMIT ?""",
                (owner, mode, account_id, region, resource_id, metric, limit)).fetchall()
        return [dict(zip(("timestamp", "value", "unit", "statistic", "period_seconds"), row))
                for row in reversed(rows)]

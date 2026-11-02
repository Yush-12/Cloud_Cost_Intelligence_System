"""Loopback-only development viewer. Hosted authentication is a later milestone."""
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from costintel.detection import analyze


def create_app(store, owner, mode, account, region):
    app = Flask(__name__)

    def current():
        data = store.latest(owner, mode, account, region)
        age = None
        if data:
            age = max(0, (datetime.now(timezone.utc) -
                          datetime.fromisoformat(data["collected_at"])).total_seconds())
        return {"mode": mode, "account_id": account, "region": region,
                "status": "not_collected" if data is None else "snapshot_available",
                "age_seconds": age, "stale": age is not None and age > 900,
                "optimization_enabled": False, "snapshot": data}

    @app.get("/api/inventory")
    def inventory():
        # Scope is server configuration, never an untrusted query parameter.
        return jsonify(current())

    @app.get("/")
    def dashboard():
        return render_template('dashboard.html', view=current(), detections=analyze(store, owner, mode, account, region),
                               actions=store.review_history(owner, mode, account, region),
                               containments=store.containment_history(owner, mode, account, region),
                               optimizations=store.optimization_history(owner, mode, account, region))

    @app.get('/api/actions')
    def action_history():
        return jsonify({'mode': mode, 'account_id': account, 'region': region,
                        'reviews': store.review_history(owner, mode, account, region),
                        'containments': store.containment_history(owner, mode, account, region),
                        'optimizations': store.optimization_history(owner, mode, account, region)})

    @app.get('/api/detection')
    def detection():
        return jsonify({'mode': mode, 'optimization_enabled': False,
                        'results': analyze(store, owner, mode, account, region)})

    @app.get("/api/metrics")
    def metrics():
        resource = request.args.get("resource_id")
        metric = request.args.get("metric")
        if not resource or not metric:
            return jsonify({"error": "resource_id and metric are required"}), 400
        return jsonify({"mode": mode, "account_id": account, "region": region,
                        "resource_id": resource, "metric": metric,
                        "points": store.history(owner, mode, account, region, resource, metric)})

    return app

"""Loopback-only development viewer, FinOps orchestration console, and ephemeral auth gateway.
"""
from datetime import datetime, timezone
from datetime import timedelta
import json
import secrets
import uuid
import time
from threading import RLock

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from costintel.detection import analyze
from costintel.inventory import collect_aws, collect_demo


def create_app(store, owner, mode, account, region, *, deployment="local", session_registry=None, session_secret=None):
    app = Flask(__name__)
    app.secret_key = session_secret or secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_TTL_SECONDS=3600)
    if deployment == 'hosted':
        if session_registry is None or not session_secret:
            raise ValueError('Hosted mode requires durable sessions and a stable secret')
        app.config.update(SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=timedelta(days=30))
    if deployment == "local":
        app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']


    # In-memory ephemeral session credentials (RAM only, never saved to disk or DB)
    active_sessions = session_registry if deployment == 'hosted' else {}
    execution_lock = RLock()

    app.extensions['workspace_sessions'] = active_sessions

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    @app.before_request
    def protect():
        now = time.monotonic()
        for sid, value in list(active_sessions.items()):
            if value.get('expires', 0) <= now:
                active_sessions.pop(sid, None)
        if request.endpoint is None:
            return None
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                return jsonify(error='Cross-origin request rejected'), 403
            if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), csrf()):
                return jsonify(error='Refresh this page before submitting'), 403
            if not request.is_json or not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='JSON object required'), 400
        public = {'login_page', 'api_login', 'session_info', 'static'}
        if request.endpoint not in public and session.get('session_id') not in active_sessions:
            if request.path.startswith('/api/'):
                return jsonify(error='Connect a workspace first'), 401
            return redirect('/login')

    @app.after_request
    def secure_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/api/session')
    def session_info():
        return jsonify(csrf_token=csrf(), authenticated=session.get('session_id') in active_sessions)

    def get_scope():
        return active_sessions[session['session_id']]

    def get_boto3_session():
        scope = get_scope()
        if scope['mode'] != 'aws' or 'sdk' not in scope:
            raise ValueError('No authenticated AWS session')
        return scope['sdk']

    def current():
        scope = get_scope()
        o = scope["owner"]
        m = scope["mode"]
        acc = scope["account_id"]
        reg = scope["region"]
        data = store.latest(o, m, acc, reg)
        age = None
        if data:
            age = max(0, (datetime.now(timezone.utc) -
                          datetime.fromisoformat(data["collected_at"])).total_seconds())
        return {
            "mode": m,
            "account_id": acc,
            "region": reg,
            "profile": scope.get("profile"),
            "status": "not_collected" if data is None else "snapshot_available",
            "age_seconds": age,
            "stale": age is not None and age > 900,
            "optimization_enabled": False,
            "snapshot": data,
            "est_monthly_savings": None,
        }

    # =========================================================================
    # Auth & Session Gateway Routes
    # =========================================================================

    @app.get("/login")
    def login_page():
        return render_template("login.html", csrf=csrf(), local=deployment != "demo", hosted=deployment == 'hosted')

    @app.post("/api/auth/login")
    def api_login():
        req = request.get_json(silent=True) or {}
        access_key = req.get("access_key_id", "").strip()
        secret_key = req.get("secret_access_key", "").strip()
        token = (req.get("session_token") or "").strip() or None
        reg = req.get("region", "ap-south-1").strip()
        req_mode = req.get("mode", "").strip()

        if deployment == 'demo' and req_mode != 'demo':
            return jsonify(error='AWS connections are unavailable in demo mode'), 403
        if deployment == 'hosted' and req_mode == 'demo':
            return jsonify(error='Connect your AWS account to use this site'), 403
        if 'profile' in req:
            return jsonify(error='Website connections require temporary credentials; use the CLI for profiles'), 400
        if req_mode != 'demo' and not token:
            return jsonify(error='Temporary credentials require a session token'), 400
        active_sessions.pop(session.get('session_id'), None)
        sid = str(uuid.uuid4())
        workspace_owner = session.setdefault('workspace_id', str(uuid.uuid4())) if deployment == 'hosted' else owner
        if deployment == 'hosted':
            session.permanent = True

        # 1. Offline Demo Mode
        if req_mode == "demo":
            active_sessions[sid] = {
                "expires": time.monotonic() + app.config["SESSION_TTL_SECONDS"],
                "owner": workspace_owner,
                "mode": "demo",
                "profile": None,
                "account_id": "demo-account",
                "region": reg,
            }
            session["session_id"] = sid
            session["logged_out"] = False
            # Ensure demo data is saved
            demo_data = collect_demo(reg)
            store.save(owner, demo_data)
            return jsonify({
                "status": "ok",
                "message": "Connected to Offline Demo Sandbox",
                "account_id": "demo-account",
                "mode": "demo",
            })

        # 2. Direct temporary credentials (Ephemeral In-Memory Only)
        if not access_key or not secret_key:
            return jsonify({"error": "AWS Access Key ID and Secret Access Key are required"}), 400

        try:
            cfg = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
            ephemeral_sess = boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=token,
                region_name=reg,
            )
            ident = ephemeral_sess.client("sts", region_name=reg, endpoint_url=f"https://sts.{reg}.amazonaws.com", config=cfg).get_caller_identity()
            if ident.get("Arn", "").endswith(":root"):
                return jsonify({"error": "Root credentials are not supported. Use a dedicated IAM user/role."}), 400

            # Store in RAM only
            active_sessions[sid] = {
                "expires": time.monotonic() + app.config["SESSION_TTL_SECONDS"],
                "owner": workspace_owner,
                "mode": "aws",
                "profile": None,
                "sdk": ephemeral_sess,
                "credentials": {
                    "aws_access_key_id": access_key,
                    "aws_secret_access_key": secret_key,
                    "aws_session_token": token,
                },
                "account_id": ident["Account"],
                "region": reg,
            }
            session["session_id"] = sid
            session["logged_out"] = False

            # Collect initial inventory
            try:
                data = collect_aws(reg, session=ephemeral_sess, expected_account=ident["Account"], with_metrics=False)
                store.save(workspace_owner, data)
            except Exception:
                if deployment == 'hosted':
                    active_sessions.pop(sid, None)
                    session.pop('session_id', None)
                    return jsonify(error='AWS identity was verified, but the hosted workspace could not save inventory. Check database availability.'), 502

            return jsonify({
                "status": "ok",
                "message": f"Successfully authenticated as {ident['Arn']}",
                "account_id": ident["Account"],
                "arn": ident["Arn"],
                "mode": "aws",
            })
        except Exception as exc:
            return jsonify({"error": f"AWS Authentication failed: {str(exc)}"}), 400

    @app.post("/api/auth/logout")
    def api_logout():
        sid = session.get("session_id")
        if sid and sid in active_sessions:
            del active_sessions[sid]
        session.clear()
        session["logged_out"] = True
        message = ('Session destroyed. Encrypted temporary credentials deleted.' if deployment == 'hosted'
                   else 'Session destroyed. Credentials purged from RAM.')
        return jsonify({"status": "ok", "message": message})

    # =========================================================================
    # Main Dashboard Route
    # =========================================================================

    @app.get("/")
    def dashboard():
        if session.get("logged_out"):
            return redirect(url_for("login_page"))

        scope = get_scope()
        o = scope["owner"]
        m = scope["mode"]
        acc = scope["account_id"]
        reg = scope["region"]
        return render_template(
            "dashboard.html",
            view=current(), csrf=csrf(), hosted=deployment == 'hosted',
            detections=analyze(store, o, m, acc, reg),
            actions=store.review_history(o, m, acc, reg),
            containments=store.containment_history(o, m, acc, reg),
            optimizations=store.optimization_history(o, m, acc, reg),
        )

    @app.get("/api/inventory")
    def inventory():
        return jsonify(current())

    @app.get("/api/actions")
    def action_history():
        scope = get_scope()
        o = scope["owner"]
        m = scope["mode"]
        acc = scope["account_id"]
        reg = scope["region"]
        return jsonify({
            "mode": m,
            "account_id": acc,
            "region": reg,
            "reviews": store.review_history(o, m, acc, reg),
            "containments": store.containment_history(o, m, acc, reg),
            "optimizations": store.optimization_history(o, m, acc, reg),
        })

    @app.get("/api/detection")
    def detection():
        scope = get_scope()
        o = scope["owner"]
        m = scope["mode"]
        acc = scope["account_id"]
        reg = scope["region"]
        return jsonify({
            "mode": m,
            "optimization_enabled": False,
            "results": analyze(store, o, m, acc, reg),
        })

    @app.get("/api/metrics")
    def metrics():
        scope = get_scope()
        o = scope["owner"]
        m = scope["mode"]
        acc = scope["account_id"]
        reg = scope["region"]
        resource = request.args.get("resource_id")
        metric = request.args.get("metric")
        if not resource or not metric:
            return jsonify({"error": "resource_id and metric are required"}), 400
        return jsonify({
            "mode": m,
            "account_id": acc,
            "region": reg,
            "resource_id": resource,
            "metric": metric,
            "points": store.history(o, m, acc, reg, resource, metric),
        })

    @app.get("/api/profiles")
    def list_profiles():
        if deployment == 'hosted':
            return jsonify(error='CLI profiles are local-only'), 403
        scope = get_scope()
        cur_mode = scope["mode"]
        cur_profile = scope.get("profile")
        cur_account = scope["account_id"]
        cur_region = scope["region"]

        discovered = []
        try:
            if deployment != "local":
                raise ValueError("Public demo")
            sess = boto3.Session()
            for p in sess.available_profiles:
                role = "AWS CLI Profile"
                if "deploy" in p:
                    role = "Deployer & Auto-Remediation (Containment)"
                elif "login" in p:
                    role = "SSO Authentication / Login Profile"
                elif "reader" in p or p == "costintel":
                    role = "CostIntel Telemetry Reader"

                is_act = (cur_mode == "aws" and (cur_profile == p or (not cur_profile and p == "costintel")))
                discovered.append({
                    "name": p,
                    "type": "aws",
                    "role": role,
                    "active": is_act,
                })
        except Exception:
            pass

        if not discovered and cur_mode == "aws":
            discovered.append({
                "name": "aws-active",
                "type": "aws",
                "role": f"Active AWS Account ({cur_account})",
                "active": True,
            })

        discovered.append({
            "name": "demo",
            "type": "demo",
            "role": "Simulated Multi-Service Environment (Offline)",
            "active": (cur_mode == "demo"),
        })

        return jsonify({
            "active": {
                "mode": cur_mode,
                "profile": cur_profile or ("costintel" if cur_mode == "aws" else "demo"),
                "account_id": cur_account,
                "region": cur_region,
            },
            "profiles": discovered,
        })

    @app.post('/api/profiles/switch')
    def switch_profile():
        return jsonify(error='Disconnect and reconnect to change AWS identity'), 409

    @app.post("/api/pipeline/run")
    def run_live_pipeline():
        scope = get_scope()
        req = request.get_json()
        if type(req.get('apply', False)) is not bool:
            return jsonify(error='apply must be a boolean'), 400
        if req.get('apply'):
            return jsonify(error='Collect and review first, then execute one returned plan'), 409
        if any(k in req for k in ('profile', 'region', 'account')):
            return jsonify(error='Reconnect to change workspace identity'), 400
        hours = req.get('window_hours', 48)
        if type(hours) is not int or not 1 <= hours <= 48:
            return jsonify(error='window_hours must be an integer from 1 to 48'), 400
        if req.get('stimulate', 'none') != 'none':
            return jsonify(error='Use the bounded lab CLI for workload experiments'), 409
        from costintel.pipeline import run_pipeline, format_summary
        with execution_lock:
            scope.pop('preview', None)
            try:
                report = run_pipeline(store=store, mode=scope['mode'], account=scope['account_id'],
                    region=scope['region'], session=get_boto3_session() if scope['mode']=='aws' else None,
                    owner=scope['owner'], expected_account=scope['account_id'], with_metrics=scope['mode']=='aws',
                    max_resources=5 if deployment == 'hosted' else 10,
                    window_hours=min(hours, 6) if deployment == 'hosted' else hours, apply=False,
                    action_type='observe' if deployment == 'hosted' else 'all')
                preview_token = secrets.token_urlsafe(32)
                scope['preview'] = {'token':preview_token, 'expires':time.monotonic()+300,
                                    'plans':{p['id']:p for p in report['plans_generated']}}
                if deployment == 'hosted':
                    active_sessions[session['session_id']] = scope
                return jsonify(status='success', report=report, summary=format_summary(report),
                               preview_token=preview_token, expires_in=300)
            except Exception:
                return jsonify(error='Collection failed. The previous saved snapshot is preserved if collection did not finish. Check AWS login and permissions.'),502

    @app.post('/api/plans/execute')
    def execute_plan():
        from costintel.actions import apply_optimization_plan
        scope = get_scope()
        req = request.get_json()
        if deployment != 'local' or scope['mode'] != 'aws' or scope['owner'] != 'local':
            return jsonify(error='Execution requires an authenticated local AWS workspace'),403
        if set(req) != {'preview_token','plan_id'} or not all(isinstance(v,str) for v in req.values()):
            return jsonify(error='Only the preview token and selected plan ID are accepted'),400
        with execution_lock:
            preview = scope.get('preview')
            if not preview or preview['expires'] < time.monotonic() or not secrets.compare_digest(preview['token'],req['preview_token']):
                return jsonify(error='Preview expired or unavailable. Collect and review again.'),409
            plan = preview['plans'].pop(req['plan_id'], None)
            if not plan:
                return jsonify(error='Plan unavailable or already submitted. Check the action history.'),409
            if plan['account'] != scope['account_id'] or plan['region'] != scope['region']:
                return jsonify(error='Plan does not belong to this workspace'),403
            try:
                result = apply_optimization_plan(store,get_boto3_session(),plan)
                return jsonify(result=result, action=plan['action'], resource=plan['resource'])
            except Exception:
                # Consume the selection even on failure; an uncertain API result must never replay automatically.
                return jsonify(error='Execution was not verified. Inspect the action history and AWS state before collecting a new plan.'),502

    @app.post("/api/workload/restore")
    def direct_restore():
        if deployment == 'hosted':
            return jsonify(error='Hosted cloud actions require durable recovery support'), 403
        scope = get_scope()
        reg = scope["region"]
        acc = scope["account_id"]
        cur_mode = scope["mode"]
        req = request.get_json(silent=True) or {}
        action_id = req.get("action_id")
        if not isinstance(action_id, str) or not action_id:
            return jsonify(error='A recorded action_id is required; bulk restoration is disabled'), 400
        if cur_mode != 'aws':
            return jsonify(error='Demo cannot restore AWS actions'), 403

        if cur_mode == "aws":
            sess = get_boto3_session()
            if action_id:
                with store.connect() as db:
                    c_row = None
                    o_row = None
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='containment_actions'").fetchone():
                        c_row = db.execute("SELECT id, plan FROM containment_actions WHERE id=?", (action_id,)).fetchone()
                    if not c_row and db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_actions'").fetchone():
                        o_row = db.execute("SELECT id, plan FROM optimization_actions WHERE id=?", (action_id,)).fetchone()

                if c_row:
                    plan = json.loads(c_row[1])
                    if plan.get("account") != acc or plan.get("region", "ap-south-1") != reg:
                        return jsonify({"error": "Action does not belong to active account"}), 403
                    try:
                        from costintel.containment import client_for, restore as restore_containment
                        c_client = client_for(sess, acc)
                        res = restore_containment(store, c_client, acc, action_id)
                        return jsonify({"status": "ok", "message": f"Containment action {action_id} restored.", "result": res})
                    except Exception as exc:
                        return jsonify({"error": f"Failed to restore containment: {str(exc)}"}), 500
                elif o_row:
                    plan = json.loads(o_row[1])
                    if plan.get("account") != acc or plan.get("region", "ap-south-1") != reg:
                        return jsonify({"error": "Action does not belong to active account"}), 403
                    try:
                        from costintel.actions import apply_start_instance
                        res = apply_start_instance(store, sess, action_id)
                        return jsonify({"status": "ok", "message": f"Optimization action {action_id} restored.", "result": res})
                    except Exception as exc:
                        return jsonify({"error": f"Failed to restore optimization: {str(exc)}"}), 500
                else:
                    return jsonify({"error": f"Action ID '{action_id}' not found"}), 404


    return app

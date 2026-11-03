"""Loopback-only development viewer, FinOps orchestration console, and ephemeral auth gateway.
"""
from datetime import datetime, timezone
import json
import secrets
import uuid
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from costintel.detection import analyze
from costintel.inventory import collect_aws, collect_demo
from costintel.provisioner import provision_lab, teardown_lab


def calculate_est_savings(store, owner, mode, account, region):
    return None  # Billing reconciliation is not implemented.


def create_app(store, owner, mode, account, region, *, deployment="local"):
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_TTL_SECONDS=3600)
    if deployment == "local":
        app.config['TRUSTED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']


    # In-memory ephemeral session credentials (RAM only, never saved to disk or DB)
    active_sessions = {}

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
        est_savings = calculate_est_savings(store, o, m, acc, reg)
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
            "est_monthly_savings": est_savings,
        }

    # =========================================================================
    # Auth & Session Gateway Routes
    # =========================================================================

    @app.get("/login")
    def login_page():
        return render_template("login.html", csrf=csrf(), local=deployment == "local")

    @app.post("/api/auth/login")
    def api_login():
        req = request.get_json(silent=True) or {}
        access_key = req.get("access_key_id", "").strip()
        secret_key = req.get("secret_access_key", "").strip()
        token = (req.get("session_token") or "").strip() or None
        reg = req.get("region", "ap-south-1").strip()
        prof = req.get("profile", "").strip()
        req_mode = req.get("mode", "").strip()

        if deployment != 'local' and req_mode != 'demo' and prof != 'demo':
            return jsonify(error='AWS connections require a local workspace'), 403
        if req_mode != 'demo' and prof != 'demo' and not prof and not token:
            return jsonify(error='Temporary credentials require a session token'), 400
        active_sessions.pop(session.get('session_id'), None)
        sid = str(uuid.uuid4())

        # 1. Offline Demo Mode
        if req_mode == "demo" or prof == "demo":
            active_sessions[sid] = {
                "expires": time.monotonic() + app.config["SESSION_TTL_SECONDS"],
                "owner": owner,
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

        # 2. Local Testing Profile Preset
        if prof and not access_key:
            try:
                cfg = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
                test_sess = boto3.Session(profile_name=prof, region_name=reg)
                ident = test_sess.client("sts", region_name=reg, config=cfg).get_caller_identity()
                if ident.get("Arn", "").endswith(":root"):
                    return jsonify({"error": "Root credentials are not supported. Use a dedicated IAM user/role."}), 400

                active_sessions[sid] = {
                    "expires": time.monotonic() + app.config["SESSION_TTL_SECONDS"],
                "owner": owner,
                    "mode": "aws",
                    "profile": prof,
                    "sdk": test_sess,
                    "account_id": ident["Account"],
                    "region": reg,
                }
                session["session_id"] = sid
                session["logged_out"] = False

                # Auto-collect current inventory
                try:
                    data = collect_aws(reg, session=test_sess, expected_account=ident["Account"], with_metrics=False)
                    store.save(owner, data)
                except Exception:
                    pass

                return jsonify({
                    "status": "ok",
                    "message": f"Connected to AWS profile '{prof}'",
                    "account_id": ident["Account"],
                    "arn": ident["Arn"],
                    "mode": "aws",
                })
            except Exception as exc:
                return jsonify({"error": f"Failed to authenticate with profile '{prof}': {str(exc)}"}), 400

        # 3. Direct IAM Credentials (Ephemeral In-Memory Only)
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
            ident = ephemeral_sess.client("sts", region_name=reg, config=cfg).get_caller_identity()
            if ident.get("Arn", "").endswith(":root"):
                return jsonify({"error": "Root credentials are not supported. Use a dedicated IAM user/role."}), 400

            # Store in RAM only
            active_sessions[sid] = {
                "expires": time.monotonic() + app.config["SESSION_TTL_SECONDS"],
                "owner": owner,
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
                store.save(owner, data)
            except Exception:
                pass

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
        return jsonify({"status": "ok", "message": "Session destroyed. Credentials purged from RAM."})

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
            view=current(), csrf=csrf(),
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

    # =========================================================================
    # Lab Resource Provisioner & Teardown Endpoints
    # =========================================================================

    @app.post('/api/lab/provision')
    @app.post('/api/lab/teardown')
    def infrastructure():
        return jsonify(error='Use a reviewed Terraform plan in infra/lambda-lab. Browser provisioning is disabled until complete cleanup is verified.'), 409

    @app.post("/api/pipeline/run")
    def run_live_pipeline():
        scope = get_scope()
        req = request.get_json(silent=True) or {}
        if type(req.get('apply', False)) is not bool:
            return jsonify(error='apply must be a boolean'), 400
        if req.get('apply'):
            return jsonify(error='Browser runs are preview-only. Apply reviewed plans with the local CLI.'), 409
        if any(k in req for k in ('profile','region','account')):
            return jsonify(error='Reconnect to change workspace identity'), 400
        if type(req.get('window_hours', 1)) is not int or not 1 <= req.get('window_hours', 1) <= 48:
            return jsonify(error='window_hours must be an integer from 1 to 48'), 400
        if req.get('stimulate', 'none') != 'none':
            return jsonify(error='Use the bounded lab CLI for workload experiments'), 409
        apply_changes = False
        stimulate = req.get("stimulate", "none")
        stim_count = int(req.get("stimulate_count", 5))
        stim_count = max(1, min(20, stim_count))
        cur_mode = scope["mode"]
        acc = scope["account_id"]
        reg = req.get("region") or scope["region"]
        prof = req.get("profile") or scope.get("profile")

        logs = []
        stimulation_res = None

        # 1. Workload stimulation
        if stimulate in ("slow", "baseline"):
            logs.append(f"[WORKLOAD STIMULATION] Invoking costintel-lab-workload ({stim_count}x, scenario='{stimulate}')...")
            if cur_mode == "aws":
                try:
                    from scripts.run_lambda_lab import run as run_lab
                    sess = get_boto3_session()
                    res = run_lab(sess, acc, count=stim_count, scenario=stimulate)
                    stimulation_res = res
                    logs.append(f"[WORKLOAD SUCCESS] Executed {res['invocations']} invocations on {res['function_name']} (Errors: {res['function_errors']})")
                except Exception as exc:
                    logs.append(f"[WORKLOAD WARNING] Lab invocation: {str(exc)}")
            else:
                stimulation_res = {
                    "scenario": stimulate,
                    "invocations": stim_count,
                    "simulated": True,
                    "function_name": "demo-lambda-workload",
                }
                logs.append(f"[WORKLOAD SIMULATED] Generated {stim_count} simulated {stimulate} executions.")

        # 2. End-to-end pipeline run
        from costintel.pipeline import run_pipeline, format_summary
        try:
            logs.append(f"[PIPELINE START] Ingesting multi-service telemetry & running Isolation Forest (mode={cur_mode}, apply={apply_changes})...")
            pipe_prof = prof
            if cur_mode == "aws" and apply_changes and not pipe_prof:
                pipe_prof = "costintel-deploy"

            report = run_pipeline(
                store=store,
                mode=cur_mode,
                account=acc,
                region=reg,
                profile=pipe_prof,
                session=get_boto3_session() if cur_mode == "aws" else None,
                owner=scope["owner"],
                expected_account=acc,
                with_metrics=(cur_mode == "aws"),
                max_resources=10,
                window_hours=int(req.get("window_hours", 1)),
                apply=apply_changes,
                action_type="all",
            )
            summary = format_summary(report)
            logs.append("[PIPELINE COMPLETE] Inventory updated, ML analysis scored, actions recorded.")
            return jsonify({
                "status": "success",
                "report": report,
                "summary": summary,
                "stimulation": stimulation_res,
                "logs": logs,
                "est_monthly_savings": calculate_est_savings(store, scope["owner"], cur_mode, acc, reg),
            })
        except Exception as exc:
            logs.append(f"[PIPELINE ERROR] {str(exc)}")
            return jsonify({
                "status": "error",
                "error": str(exc),
                "logs": logs,
            }), 500

    @app.post('/api/workload/stimulate')
    def direct_stimulate():
        return jsonify(error='Use the bounded lab CLI for workload experiments'), 409

    @app.post("/api/workload/restore")
    def direct_restore():
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

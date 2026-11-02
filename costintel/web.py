"""Loopback-only development viewer, FinOps orchestration console, and ephemeral auth gateway.
"""
from datetime import datetime, timezone
import json
import secrets
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from costintel.detection import analyze
from costintel.inventory import collect_aws, collect_demo
from costintel.provisioner import provision_lab, teardown_lab


def calculate_est_savings(store, owner, mode, account, region):
    if mode == 'demo':
        return 14.31
    total = 0.0
    opts = store.optimization_history(owner, mode, account, region)
    applied_resources = set()
    for opt in opts:
        if opt.get('status') == 'applied':
            total += opt.get('est_monthly_savings_usd', 8.47 if opt.get('action') == 'stop_idle_instance' else 0.64)
            applied_resources.add(opt.get('resource'))

    containments = store.containment_history(owner, mode, account, region)
    for c in containments:
        if c.get('status') == 'contained':
            total += 5.20
            applied_resources.add(c.get('resource'))

    reviews = store.review_history(owner, mode, account, region)
    for r in reviews:
        if r.get('status') == 'applied' and r.get('resource') not in applied_resources:
            total += 5.20
            applied_resources.add(r.get('resource'))

    if total == 0.0:
        try:
            from costintel.actions import generate_optimization_plans
            plans = generate_optimization_plans(store, account, region)
            total = sum(p.get('est_monthly_savings_usd', 0.0) for p in plans)
        except Exception:
            pass

    return round(total, 2)


def create_app(store, owner, mode, account, region):
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    # In-memory ephemeral session credentials (RAM only, never saved to disk or DB)
    active_sessions = {}

    state = {
        "owner": owner,
        "mode": mode,
        "account_id": account,
        "region": region,
        "profile": "costintel" if mode == "aws" else None,
    }

    def get_boto3_session():
        sid = session.get("session_id")
        if sid and sid in active_sessions:
            sess_data = active_sessions[sid]
            creds = sess_data.get("credentials")
            if creds:
                return boto3.Session(
                    aws_access_key_id=creds["aws_access_key_id"],
                    aws_secret_access_key=creds["aws_secret_access_key"],
                    aws_session_token=creds.get("aws_session_token"),
                    region_name=state["region"],
                )
            if sess_data.get("profile"):
                return boto3.Session(profile_name=sess_data["profile"], region_name=state["region"])
        # Default profile fallback
        if state.get("profile"):
            return boto3.Session(profile_name=state["profile"], region_name=state["region"])
        return boto3.Session(region_name=state["region"])

    def current():
        o = state["owner"]
        m = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
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
            "profile": state.get("profile"),
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
        return render_template("login.html")

    @app.post("/api/auth/login")
    def api_login():
        req = request.get_json(silent=True) or {}
        access_key = req.get("access_key_id", "").strip()
        secret_key = req.get("secret_access_key", "").strip()
        token = (req.get("session_token") or "").strip() or None
        reg = req.get("region", "ap-south-1").strip()
        prof = req.get("profile", "").strip()
        req_mode = req.get("mode", "").strip()

        sid = str(uuid.uuid4())

        # 1. Offline Demo Mode
        if req_mode == "demo" or prof == "demo":
            state["mode"] = "demo"
            state["profile"] = None
            state["account_id"] = "demo-account"
            state["region"] = reg
            active_sessions[sid] = {"mode": "demo", "account_id": "demo-account", "region": reg}
            session["session_id"] = sid
            session["logged_out"] = False
            # Ensure demo data is saved
            demo_data = collect_demo(reg)
            store.save(state["owner"], demo_data)
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

                state["mode"] = "aws"
                state["profile"] = prof
                state["account_id"] = ident["Account"]
                state["region"] = reg
                active_sessions[sid] = {"mode": "aws", "profile": prof, "account_id": ident["Account"], "region": reg}
                session["session_id"] = sid
                session["logged_out"] = False

                # Auto-collect current inventory
                try:
                    data = collect_aws(reg, session=test_sess, expected_account=ident["Account"], with_metrics=False)
                    store.save(state["owner"], data)
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

            state["mode"] = "aws"
            state["profile"] = None
            state["account_id"] = ident["Account"]
            state["region"] = reg
            # Store in RAM only
            active_sessions[sid] = {
                "mode": "aws",
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
                store.save(state["owner"], data)
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
        if sid in active_sessions:
            del active_sessions[sid]
        session["session_id"] = None
        session["logged_out"] = True
        return jsonify({"status": "ok", "message": "Session destroyed. Credentials purged from RAM."})

    # =========================================================================
    # Main Dashboard Route
    # =========================================================================

    @app.get("/")
    def dashboard():
        if session.get("logged_out"):
            return redirect(url_for("login_page"))

        o = state["owner"]
        m = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
        return render_template(
            "dashboard.html",
            view=current(),
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
        o = state["owner"]
        m = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
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
        o = state["owner"]
        m = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
        return jsonify({
            "mode": m,
            "optimization_enabled": False,
            "results": analyze(store, o, m, acc, reg),
        })

    @app.get("/api/metrics")
    def metrics():
        o = state["owner"]
        m = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
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
        discovered = []
        try:
            sess = boto3.Session()
            for p in sess.available_profiles:
                role = "AWS CLI Profile"
                if "deploy" in p:
                    role = "Deployer & Auto-Remediation (Containment)"
                elif "login" in p:
                    role = "SSO Authentication / Login Profile"
                elif "reader" in p or p == "costintel":
                    role = "CostIntel Telemetry Reader"

                is_act = (state["mode"] == "aws" and (state["profile"] == p or (not state["profile"] and p == "costintel")))
                discovered.append({
                    "name": p,
                    "type": "aws",
                    "role": role,
                    "active": is_act,
                })
        except Exception:
            pass

        if not discovered and state["mode"] == "aws":
            discovered.append({
                "name": "aws-active",
                "type": "aws",
                "role": f"Active AWS Account ({state['account_id']})",
                "active": True,
            })

        discovered.append({
            "name": "demo",
            "type": "demo",
            "role": "Simulated Multi-Service Environment (Offline)",
            "active": (state["mode"] == "demo"),
        })

        return jsonify({
            "active": {
                "mode": state["mode"],
                "profile": state.get("profile") or ("costintel" if state["mode"] == "aws" else "demo"),
                "account_id": state["account_id"],
                "region": state["region"],
            },
            "profiles": discovered,
        })

    @app.post("/api/profiles/switch")
    def switch_profile():
        req = request.get_json(silent=True) or {}
        target = req.get("profile", "").strip()
        target_region = req.get("region") or state["region"]

        if target == "demo" or req.get("mode") == "demo":
            state["mode"] = "demo"
            state["profile"] = None
            state["account_id"] = "demo-account"
            state["region"] = target_region
            return jsonify({
                "status": "ok",
                "message": "Switched to demo simulated environment",
                "active": {
                    "mode": "demo",
                    "profile": "demo",
                    "account_id": "demo-account",
                    "region": target_region,
                },
            })

        try:
            cfg = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
            sess = boto3.Session(profile_name=target, region_name=target_region)
            identity = sess.client("sts", region_name=target_region, config=cfg).get_caller_identity()
            if identity.get("Arn", "").endswith(":root"):
                return jsonify({"error": "Root credentials are not supported. Use a dedicated IAM user/role."}), 400

            state["mode"] = "aws"
            state["profile"] = target
            state["account_id"] = identity["Account"]
            state["region"] = target_region

            return jsonify({
                "status": "ok",
                "message": f"Connected to AWS profile '{target}'",
                "identity": identity["Arn"],
                "active": {
                    "mode": "aws",
                    "profile": target,
                    "account_id": identity["Account"],
                    "region": target_region,
                },
            })
        except Exception as exc:
            return jsonify({"error": f"Failed to switch to profile '{target}': {str(exc)}"}), 400

    # =========================================================================
    # Lab Resource Provisioner & Teardown Endpoints
    # =========================================================================

    @app.post("/api/lab/provision")
    def api_lab_provision():
        cur_mode = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
        if cur_mode != "aws":
            return jsonify({
                "status": "provisioned",
                "message": "Demo mode: 4 simulated multi-service resources ready.",
                "resources": [
                    {"service": "ec2", "resource_id": "demo-ec2-workload", "status": "running", "est_monthly_cost": "$8.47 / mo"},
                    {"service": "lambda", "resource_id": "demo-lambda-workload", "status": "Active", "est_monthly_cost": "$0.20 / 1M reqs"},
                    {"service": "s3", "resource_id": "costintel-telemetry-archive", "status": "available", "est_monthly_cost": "$0.023 / GB"},
                    {"service": "dynamodb", "resource_id": "costintel-billing-cache", "status": "ACTIVE", "est_monthly_cost": "Free Tier"},
                ],
            })

        try:
            sess = get_boto3_session()
            result = provision_lab(sess, acc, region=reg)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"Lab provisioning failed: {str(exc)}"}), 500

    @app.post("/api/lab/teardown")
    def api_lab_teardown():
        cur_mode = state["mode"]
        acc = state["account_id"]
        reg = state["region"]
        if cur_mode != "aws":
            return jsonify({
                "status": "completed",
                "message": "Demo lab state reset to baseline.",
            })

        try:
            sess = get_boto3_session()
            result = teardown_lab(sess, acc, region=reg)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"Lab teardown failed: {str(exc)}"}), 500

    # =========================================================================
    # Live FinOps Intelligence Pipeline Runner
    # =========================================================================

    @app.post("/api/pipeline/run")
    def run_live_pipeline():
        req = request.get_json(silent=True) or {}
        apply_changes = bool(req.get("apply", False))
        stimulate = req.get("stimulate", "none")
        stim_count = int(req.get("stimulate_count", 5))
        stim_count = max(1, min(20, stim_count))
        prof = req.get("profile") or state.get("profile")
        reg = req.get("region") or state["region"]
        cur_mode = state["mode"]
        acc = state["account_id"]

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
                "est_monthly_savings": calculate_est_savings(store, state["owner"], cur_mode, acc, reg),
            })
        except Exception as exc:
            logs.append(f"[PIPELINE ERROR] {str(exc)}")
            return jsonify({
                "status": "error",
                "error": str(exc),
                "logs": logs,
            }), 500

    @app.post("/api/workload/stimulate")
    def direct_stimulate():
        req = request.get_json(silent=True) or {}
        scenario = req.get("scenario", "slow")
        count = int(req.get("count", 5))
        count = max(1, min(20, count))
        reg = state["region"]
        acc = state["account_id"]
        cur_mode = state["mode"]

        if cur_mode == "aws":
            try:
                from scripts.run_lambda_lab import run as run_lab
                sess = get_boto3_session()
                res = run_lab(sess, acc, count=count, scenario=scenario)
                return jsonify({"status": "ok", "result": res})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
        else:
            return jsonify({
                "status": "ok",
                "result": {
                    "scenario": scenario,
                    "invocations": count,
                    "simulated": True,
                    "function_name": "demo-lambda-workload",
                },
            })

    @app.post("/api/workload/restore")
    def direct_restore():
        reg = state["region"]
        acc = state["account_id"]
        cur_mode = state["mode"]

        if cur_mode == "aws":
            try:
                cfg = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
                sess = get_boto3_session()
                client = sess.client("lambda", region_name=reg, config=cfg)
                func_arn = f"arn:aws:lambda:{reg}:{acc}:function:costintel-lab-workload"
                try:
                    client.delete_function_concurrency(FunctionName=func_arn)
                except Exception:
                    pass

                with store.connect() as db:
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='containment_actions'").fetchone():
                        db.execute("UPDATE containment_actions SET status='restored' WHERE status != 'restored'")
                    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_actions'").fetchone():
                        db.execute("UPDATE optimization_actions SET status='restored' WHERE status = 'applied'")

                return jsonify({
                    "status": "ok",
                    "message": "Function concurrency restored to baseline. Autonomous throttles removed.",
                })
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
        else:
            with store.connect() as db:
                if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='containment_actions'").fetchone():
                    db.execute("UPDATE containment_actions SET status='restored' WHERE status != 'restored'")
                if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimization_actions'").fetchone():
                    db.execute("UPDATE optimization_actions SET status='restored' WHERE status = 'applied'")
            return jsonify({
                "status": "ok",
                "message": "Demo environment baseline restored.",
            })

    return app

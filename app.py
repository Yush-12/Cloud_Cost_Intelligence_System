#!/usr/bin/env python3
"""
app.py — Unified single-process entry point for Cloud Cost Intelligence System.
Runs the Flask API + Dashboard UI, and optionally the background pipeline thread.

Usage:
  python app.py                # Runs both dashboard UI/API and background pipeline
  python app.py --api-only     # Runs only the dashboard UI/API
  python app.py --pipeline-only # Runs only the pipeline in the foreground
"""

import os
import sys
import time
import signal
import threading
import argparse
import logging
from dotenv import load_dotenv

from web.dashboard_api import app as flask_app
from src.pipeline import run_pipeline_once

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("app")

shutdown_event = threading.Event()


def pipeline_worker(interval_seconds):
    """Background worker that executes the cost telemetry pipeline on a schedule."""
    logger.info(f"Pipeline background thread active (interval: {interval_seconds}s)")
    while not shutdown_event.is_set():
        try:
            logger.info("Starting scheduled pipeline cycle...")
            run_pipeline_once()
            logger.info(f"Pipeline cycle completed. Next run in {interval_seconds}s.")
        except Exception as e:
            logger.error(f"Error during background pipeline cycle: {e}", exc_info=True)

        # Sleep in short increments to respond quickly to shutdown signals
        for _ in range(interval_seconds):
            if shutdown_event.is_set():
                break
            time.sleep(1)

    logger.info("Pipeline background thread stopped cleanly.")


def handle_shutdown(signum, frame):
    logger.info(f"Received signal {signum}. Initiating graceful shutdown...")
    shutdown_event.set()
    sys.exit(0)


def parse_args():
    parser = argparse.ArgumentParser(description="Cloud Cost Intelligence System")
    parser.add_argument("--api-only", action="store_true", help="Run only the Flask dashboard API")
    parser.add_argument("--pipeline-only", action="store_true", help="Run only the pipeline in the foreground")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("PIPELINE_INTERVAL_SECONDS", "900")),
        help="Pipeline cycle interval in seconds (default: 900)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("FLASK_HOST", "0.0.0.0"),
        help="Host to bind Flask server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("FLASK_PORT", "5000")),
        help="Port to bind Flask server (default: 5000)"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("=" * 60)
    logger.info("🚀 Cloud Cost Intelligence System")
    logger.info("=" * 60)

    if args.pipeline_only:
        logger.info("Mode: Pipeline-only")
        pipeline_worker(args.interval)
        return

    # Start background pipeline thread if not api-only
    if not args.api_only:
        logger.info(f"Mode: Full (API + background pipeline every {args.interval}s)")
        worker_thread = threading.Thread(
            target=pipeline_worker,
            args=(args.interval,),
            daemon=True,
            name="PipelineThread"
        )
        worker_thread.start()
    else:
        logger.info("Mode: API-only")

    # Start Flask API server
    display_host = "localhost" if args.host == "0.0.0.0" else args.host
    logger.info(f"Serving dashboard at: http://{display_host}:{args.port}/")
    logger.info(f"API endpoints at:     http://{display_host}:{args.port}/api/")
    logger.info("Press Ctrl+C to stop.\n")

    flask_app.run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False
    )


if __name__ == "__main__":
    main()

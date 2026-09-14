#!/usr/bin/env python3
"""
run_pipeline.py — Sequential execution of collector, anomaly detector, and optimization engine.
Supports single-run and recurring loop with graceful shutdown.
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv

from collector import run_collector
from anomaly_detector import run_detection
from optimization_engine import run_engine

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("pipeline")

pipeline_steps = [
    ("collector", run_collector),
    ("anomaly_detector", run_detection),
    ("optimization_engine", run_engine),
]

_stop_requested = False


def _signal_handler(signum, frame):
    global _stop_requested
    logger.info(f"Signal {signum} received. Stopping pipeline...")
    _stop_requested = True


def run_pipeline_once():
    """Executes one complete cycle of the pipeline: collect -> detect -> action."""
    start_ts = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 50)
    logger.info(f"Pipeline cycle started at {start_ts}")
    logger.info("=" * 50)

    for name, step_func in pipeline_steps:
        logger.info(f"▶ Running step: {name}...")
        try:
            step_func()
        except Exception as e:
            logger.error(f"Step '{name}' failed with error: {e}", exc_info=True)
            logger.warning(f"Continuing pipeline despite error in '{name}'.")

    logger.info("=" * 50)
    logger.info("Pipeline cycle finished.")
    logger.info("=" * 50)


def run_loop(interval_seconds=900):
    """Runs the pipeline continuously every interval_seconds until stopped."""
    global _stop_requested
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(f"Pipeline runner started — running every {interval_seconds}s ({interval_seconds // 60}m).")
    logger.info("Press Ctrl+C to stop.\n")

    while not _stop_requested:
        run_pipeline_once()

        logger.info(f"Waiting {interval_seconds}s until next pipeline cycle...")
        for _ in range(interval_seconds):
            if _stop_requested:
                break
            time.sleep(1)

    logger.info("Pipeline runner shut down cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Cost Intelligence Pipeline Runner")
    parser.add_argument("--once", action="store_true", help="Execute only one pipeline cycle and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("PIPELINE_INTERVAL_SECONDS", "900")),
        help="Cycle interval in seconds (default: 900)"
    )
    args = parser.parse_args()

    if args.once:
        run_pipeline_once()
    else:
        run_loop(args.interval)


if __name__ == "__main__":
    main()
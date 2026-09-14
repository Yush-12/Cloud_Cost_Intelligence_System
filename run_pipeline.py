import time
from datetime import datetime, timezone
from collector import run_collector
from anomaly_detector import run_detection
from optimization_engine import run_engine

pipeline_steps = [
    ("collector", run_collector),
    ("anomaly_detector", run_detection),
    ("optimization_engine", run_engine),
]

def run_loop(interval_seconds=900):
    print("🔄 Pipeline runner started — runs every 15 minutes")
    print("   Keep this running alongside dashboard_api.py\n")

    while True:
        print(f"\n{'='*50}")
        print(f"Pipeline run at {datetime.now(timezone.utc).isoformat()}")
        print(f"{'='*50}")
        
        for name, step_func in pipeline_steps:
            print(f"\n▶ Running {name}...")
            try:
                step_func()
            except Exception as e:
                print(f"  ⚠️  {name} raised an error: {e} — continuing pipeline")

        print(f"\n✅ Pipeline complete — next run in {interval_seconds // 60} minutes")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    run_loop()
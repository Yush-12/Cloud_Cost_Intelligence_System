"""No SDK, outbound calls, recursion, or user-controlled execution duration."""
import time


def handler(event, context):
    scenario = event.get("scenario", "baseline") if isinstance(event, dict) else "baseline"
    if scenario not in {"baseline", "slow", "error"}:
        raise ValueError("Unknown lab scenario")
    if scenario == "error":
        raise RuntimeError("Intentional costintel lab error")
    delay = 0.05 if scenario == "baseline" else 0.5
    time.sleep(delay)
    return {"lab": "costintel", "scenario": scenario, "delay_ms": int(delay * 1000)}

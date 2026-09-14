import boto3
from boto3.dynamodb.conditions import Attr
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from db_utils import scan_all

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")
dynamo = boto3.resource('dynamodb', region_name=REGION)
audit_table = dynamo.Table("OptimizationAudit")


def validate_savings():
    print("\n📊 Cost Attribution Validation")
    print("="*50)

    # Pull all actioned audit records (paginated)
    items = scan_all(audit_table, Attr("status").eq("actioned"))

    if not items:
        print("  No actioned records found — run optimization_engine.py first")
        return

    # System estimated savings
    system_estimate = sum(float(i.get("estimated_saving_usd", 0)) for i in items)
    print(f"\n  System estimated savings:  ${system_estimate:.4f}/hr")
    print(f"  Projected daily:           ${system_estimate * 24:.4f}/day")
    print(f"  Projected monthly:         ${system_estimate * 24 * 30:.2f}/month")

    # Try to pull real Cost Explorer delta if activated
    try:
        ce = boto3.client('ce', region_name='us-east-1')
        now = datetime.now(timezone.utc)

        # Compare this week vs last week
        this_week_end   = now.strftime("%Y-%m-%d")
        this_week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        last_week_start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        last_week_end   = this_week_start

        this_week = ce.get_cost_and_usage(
            TimePeriod={"Start": this_week_start, "End": this_week_end},
            Granularity="WEEKLY",
            Metrics=["BlendedCost"]
        )
        last_week = ce.get_cost_and_usage(
            TimePeriod={"Start": last_week_start, "End": last_week_end},
            Granularity="WEEKLY",
            Metrics=["BlendedCost"]
        )

        this_cost = float(this_week["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])
        last_cost = float(last_week["ResultsByTime"][0]["Total"]["BlendedCost"]["Amount"])
        real_delta = last_cost - this_cost

        print(f"\n  Real AWS Cost Explorer delta:")
        print(f"  Last week cost:   ${last_cost:.4f}")
        print(f"  This week cost:   ${this_cost:.4f}")
        print(f"  Actual saving:    ${real_delta:.4f}")

        if real_delta > 0:
            accuracy = min(100, (real_delta / max(system_estimate * 24 * 7, 0.0001)) * 100)
            print(f"\n  Attribution accuracy: {accuracy:.1f}%")
        else:
            print(f"\n  ⚠️  No measurable real-world saving yet — need more collector runtime")

    except Exception as e:
        if "OptInRequired" in str(e) or "SubscriptionRequired" in str(e):
            print("\n  ⚠️  Cost Explorer not activated yet")
            print("  Go to: AWS Console → Billing → Cost Explorer → Enable")
            print("  Re-run this validator tomorrow once it activates")
        else:
            print(f"\n  Cost Explorer error: {e}")

    print("\n" + "="*50)
    print("  Action Breakdown:")
    by_action = {}
    for item in items:
        action = item.get("action_taken", "unknown")
        saving = float(item.get("estimated_saving_usd", 0))
        by_action[action] = by_action.get(action, 0) + saving

    for action, saving in by_action.items():
        print(f"  {action:<35} ${saving:.4f}/hr")

    print("="*50)


if __name__ == "__main__":
    validate_savings()
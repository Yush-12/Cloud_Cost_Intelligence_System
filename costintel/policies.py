"""Fresh metric evidence for opt-in Lambda lab containment; not cost estimation."""
from datetime import datetime, timezone
from statistics import median
import hashlib
import json
from costintel.detection import analyze


def containment_plan(store, account, region, now=None):
    if region != 'ap-south-1':
        return None
    now = now or datetime.now(timezone.utc)
    resource = f'arn:aws:lambda:{region}:{account}:function:costintel-lab-workload'
    for result in analyze(store, 'local', 'aws', account, region):
        if result['resource_id'] != resource or result['metric'] != 'Duration' or result['status'] != 'outlier':
            continue
        if not 0 <= (now-datetime.fromisoformat(result['sample_timestamp'])).total_seconds() <= 900:
            continue
        # The detector already requires a continuous, consistent baseline tail.
        points = [p for p in result['points'] if result['baseline_start'] <= p['timestamp'] <= result['sample_timestamp']]
        if len(points) < 37 or any(p['unit'] != 'Milliseconds' or p['statistic'] != 'Average' or p['period_seconds'] != 300 for p in points):
            continue
        baseline = median(p['value'] for p in points[:-1])
        latest = points[-1]['value']
        if baseline <= 0 or latest < max(100, baseline*3):
            continue
        evidence = {'metric':'Duration','sample_timestamp':result['sample_timestamp'],
                    'baseline_median_ms':baseline,'sample_ms':latest,'score':result['score'],
                    'baseline_points':result['baseline_points']}
        key = hashlib.sha256(json.dumps([account,region,resource,evidence],sort_keys=True).encode()).hexdigest()
        # Even a different outlier cannot trigger repeated containment within an hour.
        for action in store.containment_history('local','aws',account,region):
            if action['resource'] == resource and (now-datetime.fromisoformat(action['created_at'])).total_seconds() < 3600:
                return None
        return {'id':key,'action':'contain_lambda','account':account,'region':region,'resource':resource,
                'reason':'Fresh duration outlier exceeds 3x baseline and 100 ms; investigate workload context.',
                'effect':'Sets reserved concurrency to 0, blocking new executions until explicitly restored.',
                'required_tags':{'CostIntelLab':'true','CostIntelContainment':'true'},
                'evidence':evidence,'est_monthly_savings_usd':None,'reversible':True}
    return None


def apply_detected_containment(store, sdk, plan):
    from costintel.containment import apply, client_for, initialize
    initialize(store)
    with store.connect() as db:
        row=db.execute('SELECT plan,status FROM containment_actions WHERE id=?',(plan['id'],)).fetchone()
    if row:
        old=json.loads(row[0])
        if old['account']!=plan['account'] or old['resource']!=plan['resource']:
            raise ValueError('Containment scope mismatch')
        return {'id':plan['id'],'status':row[1],'replayed':False}
    current=containment_plan(store,plan['account'],plan['region'])
    if not current or current['id']!=plan['id']:
        raise ValueError('Anomaly evidence expired or changed; collect and review again')
    return apply(store,client_for(sdk,plan['account']),plan['account'],action_id=plan['id'],evidence=plan['evidence'])

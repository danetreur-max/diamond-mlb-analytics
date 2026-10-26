import json,urllib.request,time,statistics,sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1]
out=root/'portfolio-demo';out.mkdir(parents=True,exist_ok=True)
def get(path):
 with urllib.request.urlopen('http://127.0.0.1:8000/api'+path,timeout=90) as r:return json.load(r)
data={'exported_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'team_id':144,'season':2026,'summary':get('/summary'),'contracts':get('/contracts/teams/144?season=2026'),'validation':get('/contracts/decision-validation')}
(out/'snapshot.json').write_text(json.dumps(data,separators=(',',':')),encoding='utf-8')
metrics=[]
for path in ('/health','/summary','/teams','/contracts/teams/144?season=2026'):
 times=[]
 for _ in range(5):
  start=time.perf_counter();get(path);times.append(round((time.perf_counter()-start)*1000,2))
 metrics.append({'endpoint':'/api'+path,'samples':5,'median_ms':round(statistics.median(times),2),'maximum_ms':max(times),'minimum_ms':min(times)})
report={'measured_at':data['exported_at'],'environment':'Single local Windows instance, SQLite, existing data/cache, sequential HTTP loopback requests; not load testing. Cache may be warmed. No throughput or production latency claim.','summary':data['summary'],'http_timings':metrics}
(root/'reports').mkdir(exist_ok=True)
(root/'reports/performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))

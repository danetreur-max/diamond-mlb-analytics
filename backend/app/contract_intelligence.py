"""Free, scope-aware contract context and explicitly reconstructed benchmarks."""
import csv,json,re,math
from pathlib import Path
from datetime import date,datetime,timezone
from collections import defaultdict
from statistics import median
from fastapi import APIRouter,Query,HTTPException
from sqlalchemy import select
from .db import Session,Payload,Player,now
from .explore import snapshot,schedule,TODAY

router=APIRouter(prefix='/api/contracts',tags=['Contract intelligence'])
BR_TEAMS={'LAA':108,'ARI':109,'BAL':110,'BOS':111,'CHC':112,'CIN':113,'CLE':114,'COL':115,'DET':116,'HOU':117,'KCR':118,'LAD':119,'WSN':120,'WSH':120,'NYM':121,'OAK':133,'ATH':133,'PIT':134,'SDP':135,'SEA':136,'SFG':137,'STL':138,'TBR':139,'TEX':140,'TOR':141,'MIN':142,'MIN':142,'PHI':143,'ATL':144,'CHW':145,'MIA':146,'NYY':147,'MIL':158}

def num(v):
    try:
        n=float(v);return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None

def historical_baseline(history,season):
    rows=[dict(season=y,war=history[y],weight=w) for y,w in ((season-1,5),(season-2,3),(season-3,1)) if y in history]
    if not rows:return dict(available=False,reason='No prior MLB WAR history in the three-year window.')
    value=sum(r['war']*r['weight'] for r in rows)/sum(r['weight'] for r in rows)
    return dict(available=True,war=round(value,3),history=rows,input_cutoff=f'{season-1}-12-31',method='Prior-season weighted WAR benchmark v1',limited_history=len(rows)<3,
        note='5:3:1 weights on available prior seasons; missing seasons are omitted, not zero. No age, injury or role adjustment. Retrospectively reconstructed from downloaded historical bWAR; not an archived preseason forecast or a validated projection.')

def public_projection_rows(html,season,discipline):
    title=re.search(r'<title[^>]*>(.*?)</title>',html,re.S)
    if not title or f'{season} Projections' not in title.group(1):raise ValueError('Public projection page does not match the selected year.')
    match=re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
    if not match:raise ValueError('Projection source structure is unavailable.')
    data=json.loads(match.group(1));rows=None
    for q in data['props']['pageProps']['dehydratedState']['queries']:
        key=q.get('queryKey',[])
        if key and key[0]=='/projections' and key[1].get('type')=='steamer' and key[1].get('stats')==discipline:
            rows=q['state']['data'];break
    if not isinstance(rows,list):raise ValueError('Public Steamer table was not present.')
    result={};duplicates=set()
    for r in rows:
        pid=num(r.get('xMLBAMID'));war=num(r.get('WAR'))
        if not pid or war is None:continue
        pid=int(pid)
        if pid in result:duplicates.add(pid)
        result[pid]=dict(war=war,pa=num(r.get('PA')),ip=num(r.get('IP')))
    return {k:v for k,v in result.items() if k not in duplicates}

def build(cache_dir,years=(2025,2026),public_files=None):
    cache=Path(cache_dir);all_war=defaultdict(lambda:defaultdict(float));team_war=defaultdict(float);ages={};workloads=defaultdict(lambda:dict(pa=0,outs=0))
    files=[('batting',cache/'war-batting.csv'),('pitching',cache/'war-pitching.csv')]
    if not all(p.exists() for _,p in files):raise ValueError('Run public financial refresh before building intelligence.')
    seen=set()
    for discipline,path in files:
        with path.open(encoding='utf-8-sig',newline='') as handle:
            for r in csv.DictReader(handle):
                year=num(r.get('year_ID'));pid=num(r.get('mlb_ID'));war=num(r.get('WAR'));club=r.get('team_ID','')
                if year is None or pid is None or war is None or year<min(years)-3 or year>max(years) or club=='TOT' or re.fullmatch(r'\d+TM',club):continue
                y,pid=int(year),int(pid);key=(discipline,y,pid,club,r.get('stint_ID'))
                if key in seen:raise ValueError('Duplicate source WAR stint.')
                seen.add(key);all_war[pid][y]+=war
                if club in BR_TEAMS:team_war[(y,BR_TEAMS[club],pid)]+=war
                age=num(r.get('age'))
                if age is not None:ages[(y,pid)]=int(age)
                if discipline=='batting':workloads[(y,pid)]['pa']+=int(num(r.get('PA')) or 0)
                else:workloads[(y,pid)]['outs']+=int(num(r.get('IPouts')) or 0)
    forecasts={};forecast_error=None
    if public_files:
        try:
            parts={d:public_projection_rows(Path(path).read_text(encoding='utf-8'),max(years),d) for d,path in public_files.items()}
            for pid in set(parts.get('bat',{}))|set(parts.get('pit',{})):
                forecasts[pid]=dict(war=sum(parts[d][pid]['war'] for d in parts if pid in parts[d]),provider='Steamer / fWAR',source='https://www.fangraphs.com/projections?type=steamer',snapshot='Public preseason-labelled current-year table',original_publication_date=None,
                    note='Current publicly readable source, retrieval snapshot; original preseason release date is not established. fWAR-based forecast must not be subtracted from bWAR actuals.')
        except (ValueError,KeyError,TypeError) as exc:forecast_error=str(exc)
    stamp=now()
    for forecast in forecasts.values():forecast['retrieved_at']=stamp
    with Session.begin() as session:
        for year in years:
            value=dict(season=year,generated_at=stamp,war_downloaded_at=datetime.fromtimestamp(min(p.stat().st_mtime for _,p in files),timezone.utc).isoformat(),forecast_error=forecast_error,
                players={str(pid):dict(generated_at=stamp,baseline=historical_baseline(history,year),season_age=ages.get((year,pid)),workload=workloads.get((year,pid)),public_forecast=forecasts.get(pid) if year==max(years) else None) for pid,history in all_war.items()},
                team_war={f'{tid}:{pid}':round(war,4) for (y,tid,pid),war in team_war.items() if y==year})
            key=f'contracts:intelligence:{year}';row=session.get(Payload,key)
            if row:row.payload=value;row.fetched_at=stamp
            else:session.add(Payload(endpoint=key,payload=value,fetched_at=stamp))
    report=dict(years=list(years),players=len(all_war),forecasts=len(forecasts),forecast_error=forecast_error)
    with Session.begin() as session:
        status=session.get(Payload,'contracts:public-refresh')
        if status:status.payload={**status.payload,'intelligence':report}
    return report

def position_group(pos):return 'P' if pos=='P' else 'C' if pos=='C' else 'IF' if pos in ('1B','2B','3B','SS') else 'OF' if pos in ('LF','CF','RF','OF') else pos

def peer_context(target,pool,age,info):
    c=target.get('contract') or {};pid=target['id'];pos=position_group(target['position']);unit='outs' if pos=='P' else 'pa';work=(info.get(str(pid),{}).get('workload') or {}).get(unit)
    if age is None or not work or c.get('actual_war') is None or not c.get('contract_status'):
        return dict(available=False,reason='Peers require season age, workload, actual WAR and contract status.')
    candidates=[]
    for p in pool:
        if p['id']==pid or p['position_group']!=pos or p['provider']!=c.get('war_provider') or p['status']!=c.get('contract_status'):continue
        if p['age'] is None or abs(p['age']-age)>3 or not p['work'] or not .5<=p['work']/work<=2:continue
        candidates.append(p)
    if len(candidates)<5:return dict(available=False,count=len(candidates),reason='Fewer than five peers match position group, age ±3, contract status, WAR provider and 0.5–2× workload.')
    scale=540 if unit=='outs' else 600;rate=c['actual_war']/work*scale
    rates=[p['war']/p['work']*scale for p in candidates]
    return dict(available=True,count=len(candidates),median_salary=median(p['salary'] for p in candidates),median_war_rate=median(rates),player_war_rate=rate,production_percentile=sum(r<=rate for r in rates)/len(rates)*100,unit='WAR / 180 IP' if unit=='outs' else 'WAR / 600 PA',items=[dict(p,rate=p['war']/p['work']*scale) for p in sorted(candidates,key=lambda p:p['war']/p['work'],reverse=True)],
        note='Descriptive peers: same position group and contract status, age ±3, same WAR provider, 0.5–2× workload; confirmed full-season salary only. Position uses the current MLB directory. Small WAR differences are not definitive.')

def make_pool(records,positions,info):
    by_id=defaultdict(list)
    for r in records:
        v=r.values
        if v.get('salary_basis','full-season')=='full-season' and v.get('salary') is not None and v.get('actual_war') is not None:by_id[r.player_id].append(v)
    with Session() as session:
        names=dict(session.execute(select(Player.id,Player.full_name)).all())
    pool=[]
    for pid,rows in by_id.items():
        if len({(v['salary'],v['actual_war'],v.get('war_provider'),v.get('contract_status')) for v in rows})!=1:continue
        v=rows[0];pos=position_group(positions.get(pid,''));p=info.get(str(pid),{});work=p.get('workload') or {}
        pool.append(dict(id=pid,full_name=names.get(pid),position_group=pos,provider=v.get('war_provider'),status=v.get('contract_status'),salary=v['salary'],war=v['actual_war'],age=p.get('season_age'),work=work.get('outs' if pos=='P' else 'pa')))
    return pool

def quality_flags(row,season,today=None):
    today=today or TODAY();v=row.get('contract') or {};flags=[]
    if v.get('salary') is None:flags.append('Missing salary')
    if v.get('actual_war') is None:flags.append('Missing actual WAR')
    if v.get('salary_basis')=='estimated':flags.append('Estimated salary')
    if v.get('salary_basis')=='team-payroll':flags.append('Allocated/prorated payroll amount')
    for field,days,label in [('contract_as_of',30,'Contract snapshot over 30 days old'),('actual_as_of',7,'Current-year WAR snapshot over 7 days old')]:
        raw=v.get(field)
        if raw and (field!='actual_as_of' or season==today.year) and (today-date.fromisoformat(raw)).days>days:flags.append(label)
    if v.get('projected_war') is not None and not v.get('projection_date'):flags.append('Undated analyst projection')
    for mlb,actual in [('mlb_pa','actual_pa'),('mlb_outs','actual_outs')]:
        a,b=row.get(mlb),v.get(actual)
        if a is not None and b is not None and abs(a-b)>max(5,.05*max(a,b)):flags.append('MLB workload and WAR snapshot differ');break
    if row.get('team_war') is None:flags.append('No matched club WAR stint')
    if v.get('team_salary') is None:flags.append('No sourced club salary charge')
    if row.get('intelligence',{}).get('public_forecast'):flags.append('Public forecast date unverified; fWAR vs bWAR mismatch')
    return flags

def availability_summary(events,start,end,team_id=None):
    relevant=[];open_date=None;intervals=[];uncertain=False
    for e in sorted(events,key=lambda r:(r.get('effectiveDate') or r.get('date',''),r.get('id',0))):
        desc=e.get('description','');text=desc.lower();day=date.fromisoformat((e.get('effectiveDate') or e['date'])[:10])
        if day>end:continue
        is_il='injured list' in text or 'disabled list' in text
        if is_il or any(t in text for t in ('rehab assignment','traded','optioned','recalled')):relevant.append(e)
        if open_date is not None and team_id is not None and e.get('fromTeam',{}).get('id')==team_id and e.get('toTeam',{}).get('id')!=team_id and 'traded' in text:
            intervals.append((max(start,open_date),min(end,day)));open_date=None;uncertain=True
        if is_il and 'activated' in text:
            if open_date is not None:intervals.append((max(start,open_date),min(end,day)));open_date=None
            else:uncertain=True
        elif is_il and 'placed' in text and open_date is None:
            open_date=day
            if 'retroactive' in text:uncertain=True
        elif is_il and 'transferred' in text and open_date is None:uncertain=True
    if open_date is not None:intervals.append((max(start,open_date),end))
    days=sum(max(0,(b-a).days) for a,b in intervals)
    return dict(reported_il_days=days if intervals else None,open_interval=open_date is not None,incomplete=uncertain,transactions=relevant,
        note='Calendar days overlapping the regular season between reported IL placement and activation, not games missed. Retroactive dates and missing boundary events may undercount; rehab is not activation. No medical diagnosis or cause of performance is inferred.')

@router.get('/players/{player_id}/context')
def player_context(player_id:int,team_id:int,season:int=Query(2026,ge=2020,le=2100)):
    data,stamp,stale=snapshot('/transactions',{'teamId':team_id,'startDate':f'{season-1}-10-01','endDate':str(min(TODAY(),date(season,12,31)))},ttl=3600)
    games,_,schedule_stale=schedule(season,team_id);regular=[g['official_date'] for g in games if g['game_type']=='R']
    start=date.fromisoformat(min(regular)) if regular else date(season,3,1);end=min(TODAY(),date.fromisoformat(max(regular)) if regular else date(season,10,31))
    events=[e for e in data.get('transactions',[]) if e.get('person',{}).get('id')==player_id]
    return dict(**availability_summary(events,start,end,team_id),fetched_at=stamp,stale=stale or schedule_stale,source='Official MLB team transaction feed; team-specific reported events',period_start=str(start),period_end=str(end))


def validate_benchmark(histories, seasons):
    """Rolling-origin retrospective error; never use target-year inputs."""
    result=[]
    for year in seasons:
        pairs=[]
        for history in histories.values():
            benchmark=historical_baseline(history,year)
            if year in history and benchmark['available']:
                pairs.append((benchmark['war'],history[year]))
        errors=[actual-pred for pred,actual in pairs]
        result.append(dict(season=year,count=len(errors),mae=sum(abs(e) for e in errors)/len(errors) if errors else None,
            bias=sum(errors)/len(errors) if errors else None,rmse=(sum(e*e for e in errors)/len(errors))**.5 if errors else None,
            within_one=sum(abs(e)<=1 for e in errors)/len(errors) if errors else None))
    return result

@router.get('/decision-validation')
def decision_validation():
    cache=Path(__file__).resolve().parents[1]/'financial-source-cache'
    histories=defaultdict(lambda:defaultdict(float));seen=set()
    for discipline in ('batting','pitching'):
        path=cache/f'war-{discipline}.csv'
        if not path.exists():raise HTTPException(503,'Historical WAR cache unavailable. Refresh public financial sources.')
        with path.open(encoding='utf-8-sig',newline='') as handle:
            for r in csv.DictReader(handle):
                y,pid,war=num(r.get('year_ID')),num(r.get('mlb_ID')),num(r.get('WAR'));club=r.get('team_ID','')
                if y is None or pid is None or war is None or club=='TOT' or re.fullmatch(r'\d+TM',club):continue
                key=(discipline,y,pid,club,r.get('stint_ID'))
                if key in seen:raise HTTPException(503,'Duplicate historical WAR stint; validation withheld.')
                seen.add(key);histories[int(pid)][int(y)]+=war
    years=sorted({y for h in histories.values() for y in h if 2022<=y<TODAY().year})
    return dict(items=validate_benchmark(histories,years),generated_at=now(),provider='Baseball Reference bWAR',
        note='Rolling prior-three-year 5:3:1 benchmark, evaluated only on completed calendar years with an observed target-year MLB WAR record. Missing seasons are omitted. This excludes players with no target-year MLB record (survivorship bias), uses retrospectively revised source data, and is not an independent archived forecast test. MAE is typical absolute error, not a confidence interval. No salary-based expectation is inferred.')

@router.get('/players/{player_id}/contract-history')
def contract_history(player_id:int):
    from .contracts import ContractRecord
    with Session() as session:
        rows=list(session.scalars(select(ContractRecord).where(ContractRecord.player_id==player_id).order_by(ContractRecord.season,ContractRecord.team_id)))
        return dict(items=[dict(r.values,season=r.season,team_id=r.team_id,updated_at=r.updated_at) for r in rows],
            note='Stored annual snapshots, not the original signed contract schedule. Remaining guarantees exclude unverified options, retained salary, deferrals and buyouts unless explicitly supplied in source notes. Rows across clubs must not be added as annual player salary.')

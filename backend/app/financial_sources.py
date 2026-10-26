"""Manual, repeatable public-source financial refresh. Never synthesizes projections."""
import argparse,csv,hashlib,json,re,time,math,os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import defaultdict
from datetime import date,datetime,timezone
import httpx
from sqlalchemy import select
from .db import Session,Player,Team,Payload,now
from .contracts import InputRow,ContractRecord,ContractImport

TEAMS={108:'angels',109:'diamondbacks',110:'orioles',111:'red-sox',112:'cubs',113:'reds',114:'guardians',115:'rockies',116:'tigers',117:'astros',118:'royals',119:'dodgers',120:'nationals',121:'mets',133:'athletics',134:'pirates',135:'padres',136:'mariners',137:'giants',138:'cardinals',139:'rays',140:'rangers',141:'blue-jays',142:'twins',143:'phillies',144:'braves',145:'white-sox',146:'marlins',147:'yankees',158:'brewers'}
WAR_URLS={'batting':'https://www.baseball-reference.com/data/war_daily_bat.txt','pitching':'https://www.baseball-reference.com/data/war_daily_pitch.txt'}

def snapshot_day(stamp):
    return datetime.fromisoformat(stamp).astimezone().date()

def numeric(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def payroll_data(html,season):
    match=re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',html,re.S)
    if not match:raise ValueError('Payroll page has no structured source data.')
    state=json.loads(match.group(1))
    for q in state['props']['pageProps']['dehydratedState']['queries']:
        key=q.get('queryKey',[])
        if key and 'payroll' in str(key[0]) and isinstance(key[-1],dict) and key[-1].get('season')==season:
            data=q['state']['data']
            if not isinstance(data.get('dataContract'),list) or not data['dataContract']:raise ValueError('Payroll contract table is empty.')
            return data,key[-1]['teamid']
    raise ValueError('Payroll response does not match the requested season.')

def payroll_records(data,source_team,team_id,season,stamp):
    by_player=defaultdict(list)
    for item in data['dataContract']:
        pid=item.get('contractSummary',{}).get('MLBAMID')
        if isinstance(pid,int) and pid>0:by_player[pid].append(item)
    result=[]
    for pid,contracts in by_player.items():
        current,future={},{}
        descriptions=[]
        for item in contracts:
            summary=item['contractSummary'];descriptions.append(item.get('description',''))
            for year in item.get('contractYears',[]):
                if year.get('TeamId')!=source_team:continue
                amount=numeric(year.get('Salary'));year_id=year.get('Season');key=(year.get('ContractId'),year_id)
                if amount is None or amount<0:continue
                if year_id==season:current[key]=(year,summary,item)
                elif year_id>season and year.get('Type')=='GUARANTEED' and not year.get('isEstimate'):
                    future[key]=year
        if not current:continue
        lines=list(current.values());salary=sum(numeric(y['Salary']) for y,_,_ in lines)
        estimated=any(y.get('isEstimate') or s.get('isEstimate') for y,s,_ in lines)
        prorated=len(lines)>1 or any(s.get('SigningTeamID')!=source_team or s.get('payrollType')=='not40man' or re.search(r'prorat|split|retai|traded|paid by|cash',str(item.get('description',''))+' '+str(s.get('LongContractSummaryPayrollNote') or ''),re.I) for _,s,item in lines)
        basis='estimated' if estimated else 'team-payroll' if prorated else 'full-season'
        def total(field):
            numbers=[numeric(y.get(field)) for y,_,_ in lines]
            return sum(numbers) if all(n is not None and n>=0 for n in numbers) else None
        statuses=[s.get('payrollType') for _,s,_ in lines]
        status='pre-arbitration' if 'arbineligible' in statuses else 'arbitration' if 'arbeligible' in statuses else 'extension' if any(s.get('ContractType')=='Extension' for _,s,_ in lines) else 'free-agent' if any('Free Agent' in str(s.get('ContractType')) for _,s,_ in lines) else 'other'
        # Future commitments only for the current selected season. Historical pages
        # are retrieved now and cannot reconstruct past knowledge of guarantees.
        future_known=season==snapshot_day(stamp).year
        record=dict(season=season,team_id=team_id,player_id=pid,salary=salary,aav=total('AAV'),tax_payroll=total('TotalCBTHit'),salary_basis=basis,
            contract_status=status,salary_source=f'https://www.fangraphs.com/roster-resource/payroll/{TEAMS[team_id]}?season={season}',contract_as_of=str(snapshot_day(stamp)),
            remaining_years=len({y['Season'] for y in future.values()}) if future_known else None,
            remaining_guarantee=sum(numeric(y['Salary']) for y in future.values()) if future_known else None,
            notes=('RosterResource: '+' | '.join(dict.fromkeys(descriptions))+'. ' +
            'Future guarantee is the sum of listed GUARANTEED salary in seasons after the selected year; excludes current-year unpaid salary, options, buyouts and unlisted deferrals. '+
            ('Estimated salary; not confirmed. ' if estimated else '')+('Source payroll amount may be prorated, split or allocated to this club; cost per full-player-season WAR is omitted. ' if prorated else '')+
            'Page retrieved '+stamp+'. Historical salary is a retrospective source snapshot.'))
        record['team_salary']=salary
        record['team_salary_source']=record['salary_source']
        result.append(record)
    return result

def read_war(content,years):
    result=defaultdict(lambda:dict(war=0.,actual_pa=0,actual_outs=0,components=set(),rows=set()))
    for discipline,text in content.items():
        for r in csv.DictReader(text.splitlines()):
            year=numeric(r.get('year_ID'));pid=numeric(r.get('mlb_ID'));war=numeric(r.get('WAR'))
            if year not in years or not pid or war is None:continue
            # Stint rows are additive; all-team totals must not be added again.
            if str(r.get('team_ID','')).upper() in ('TOT','2TM','3TM','4TM','5TM'):continue
            key=(discipline,r.get('team_ID'),r.get('stint_ID'))
            target=result[(int(year),int(pid))]
            if key in target['rows']:raise ValueError(f'Duplicate WAR stint: {year}/{pid}/{key}')
            target['rows'].add(key);target['components'].add(discipline);target['war']+=war
            if discipline=='batting':target['actual_pa']+=int(numeric(r.get('PA')) or 0)
            else:target['actual_outs']+=int(numeric(r.get('IPouts')) or 0)
    return result

def merge_source_record(existing,fresh):
    incoming={k:v for k,v in fresh.items() if v is not None}
    if existing.get('war_provider') and incoming.get('war_provider') and existing['war_provider']!=incoming['war_provider']:
        for key in ('actual_war','war_provider','war_source','actual_as_of','actual_pa','actual_outs'):
            incoming.pop(key,None)
    merged={**existing,**incoming}
    if incoming.get('actual_as_of') and (existing.get('actual_as_of')!=incoming['actual_as_of'] or existing.get('actual_war')!=incoming.get('actual_war')):
        merged['completed_team_games']=None;merged['total_team_games']=None
    return merged

def fetch(client,url,path,force=False):
    if not force and path.exists() and time.time()-path.stat().st_mtime<3600:return path.read_text(encoding='utf-8-sig')
    for attempt in range(3):
        try:
            response=client.get(url);response.raise_for_status()
            response.encoding='utf-8'
            path.write_text(response.text,encoding='utf-8');return response.text
        except (httpx.HTTPError,OSError):
            if attempt==2:raise
            time.sleep(1+attempt)

def refresh(years,cache_dir,force=False,cached_only=False):
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True);stamp=now()
    with Session() as session:
        known={p.id for p in session.scalars(select(Player))}
    jobs=[(year,tid) for year in years for tid in TEAMS]
    records,failures,unknown=[],[],[]
    stale_pages=[]
    with httpx.Client(timeout=60,follow_redirects=True,headers={'User-Agent':'DiamondMLBExplorer/1.0 public-source research'}) as client:
        def task(job):
            year,tid=job;url=f'https://www.fangraphs.com/roster-resource/payroll/{TEAMS[tid]}?season={year}'
            path=cache/f'payroll-{year}-{tid}.html'
            try:
                text=path.read_text(encoding='utf-8-sig') if cached_only else fetch(client,url,path,force)
            except httpx.HTTPError as exc:
                if not path.exists():raise
                stale_pages.append(dict(season=year,team_id=tid,error=str(exc)))
                text=path.read_text(encoding='utf-8-sig')
            data,source_team=payroll_data(text,year)
            retrieved=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()
            return payroll_records(data,source_team,tid,year,retrieved)

        with ThreadPoolExecutor(max_workers=3) as pool:
            pending={pool.submit(task,j):j for j in jobs}
            for count,future in enumerate(as_completed(pending),1):
                job=pending[future]
                try:records.extend(future.result())
                except Exception as exc:failures.append(dict(season=job[0],team_id=job[1],error=str(exc)))
                print(json.dumps(dict(phase='payroll',completed=count,total=len(jobs),records=len(records),failures=len(failures))),flush=True)
        war_text={};war_error=None
        try:
            for d,url in WAR_URLS.items():
                path=cache/f'war-{d}.csv'
                war_text[d]=path.read_text(encoding='utf-8-sig') if cached_only else fetch(client,url,path,force)
            war=read_war(war_text,set(years))
            if not all(any(y==year for y,_ in war) for year in years):raise ValueError('WAR files do not cover every requested season.')
        except Exception as exc:war={};war_error=str(exc)
    # Resolve new source MLB IDs through the official identity endpoint, never
    # through fuzzy names. A failed identity lookup remains an explicit skip.
    missing=sorted({r['player_id'] for r in records}-known)
    if missing:
        with httpx.Client(timeout=35) as client:
            for offset in range(0,len(missing),50):
                try:
                    response=client.get('https://statsapi.mlb.com/api/v1/people',params={'personIds':','.join(map(str,missing[offset:offset+50]))});response.raise_for_status()
                    with Session.begin() as session:
                        for person in response.json().get('people',[]):
                            pid=person.get('id')
                            if pid not in missing or not person.get('fullName'):continue
                            if not session.get(Player,pid):session.add(Player(id=pid,full_name=person['fullName'],primary_position=person.get('primaryPosition',{}).get('abbreviation',''),bat_side=person.get('batSide',{}).get('code',''),throw_hand=person.get('pitchHand',{}).get('code','')))
                            known.add(pid)
                except (httpx.HTTPError,ValueError):pass
    war_stamp=datetime.fromtimestamp(min((cache/f'war-{d}.csv').stat().st_mtime for d in WAR_URLS),timezone.utc).isoformat() if war else stamp
    prepared=[]
    for r in records:
        if r['player_id'] not in known:
            unknown.append(dict(season=r['season'],team_id=r['team_id'],player_id=r['player_id']));continue
        production=war.get((r['season'],r['player_id']))
        if production:
            r.update(actual_war=round(production['war'],4),war_provider='Baseball Reference bWAR',war_source='https://www.baseball-reference.com/about/war_explained.shtml',actual_as_of=f"{r['season']}-12-31" if r['season']<snapshot_day(stamp).year else str(snapshot_day(war_stamp)),actual_pa=production['actual_pa'],actual_outs=production['actual_outs'])
            r['notes']+=' bWAR sums batting and pitching stint rows across all teams; absent WAR is not filled with zero. Retrieved '+stamp+'.'
        prepared.append(InputRow.model_validate(r).model_dump(mode='json'))
    # Automated refresh merges owned financial/source fields, preserving manual
    # projections, dates and other analyst inputs rather than replacing them.
    with Session.begin() as session:
        for r in prepared:
            key=(r['season'],r['team_id'],r['player_id']);existing=session.get(ContractRecord,key)
            merged=merge_source_record(existing.values if existing else {},r)
            if existing:existing.values=merged;existing.updated_at=stamp
            else:session.add(ContractRecord(season=r['season'],team_id=r['team_id'],player_id=r['player_id'],values=merged,updated_at=stamp))
        session.add(ContractImport(digest=hashlib.sha256(json.dumps(prepared,sort_keys=True).encode()).hexdigest(),imported_at=stamp,records=prepared))
        report=dict(years=years,fetched_at=stamp,records=len(prepared),war_records=sum(r.get('actual_war') is not None for r in prepared),failures=failures,unknown_ids=unknown,war_error=war_error,sources=list(WAR_URLS.values()),payroll_pages=len(jobs)-len(failures),stale_pages=stale_pages,cached_only=cached_only)
        key='contracts:public-refresh';payload=session.get(Payload,key)
        if payload:payload.payload=report;payload.fetched_at=stamp
        else:session.add(Payload(endpoint=key,payload=report,fetched_at=stamp))
    (cache/'latest-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    try:
        from .contract_intelligence import build
        public={}
        with httpx.Client(timeout=45,follow_redirects=True) as client:
            for discipline in ('bat','pit'):
                path=cache/f'public-steamer-{discipline}.html'
                if not cached_only:fetch(client,f'https://www.fangraphs.com/projections?type=steamer&stats={discipline}',path,force)
                public[discipline]=path
        report['intelligence']=build(cache,years,public)
    except Exception as exc:report['intelligence_error']=str(exc)
    with Session.begin() as session:
        saved=session.get(Payload,'contracts:public-refresh')
        saved.payload=report
    (cache/'latest-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True);return report

def source_lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b');handle.seek(0)
    if path.stat().st_size==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close();return None
    return handle

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--years',nargs='+',type=int,default=[2025,2026]);parser.add_argument('--cache-dir',default='financial-source-cache');parser.add_argument('--force',action='store_true');parser.add_argument('--cached-only',action='store_true');args=parser.parse_args()
    lock=source_lock(Path(args.cache_dir)/'refresh.lock')
    if lock is None:raise SystemExit('A financial source refresh is already running.')
    try:refresh(args.years,args.cache_dir,args.force,args.cached_only)
    finally:lock.close()

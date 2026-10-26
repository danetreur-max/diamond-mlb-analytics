"""Source-tracked contract and production inputs; independent of MLB game stats."""
import csv, io, hashlib, math
from datetime import date
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, Session, Player, Team, Payload, now
from .explore import roster, TODAY

router=APIRouter(prefix='/api/contracts',tags=['Contract analysis'])

class ContractRecord(Base):
    __tablename__='contract_analysis_records'
    season:Mapped[int]=mapped_column(primary_key=True)
    team_id:Mapped[int]=mapped_column(primary_key=True)
    player_id:Mapped[int]=mapped_column(primary_key=True)
    values:Mapped[dict]=mapped_column(JSON)
    updated_at:Mapped[str]

class ContractImport(Base):
    __tablename__='contract_analysis_imports'
    id:Mapped[int]=mapped_column(primary_key=True)
    digest:Mapped[str]
    imported_at:Mapped[str]
    records:Mapped[list]=mapped_column(JSON)

class InputRow(BaseModel):
    season:int=Field(ge=2020,le=2100)
    team_id:int=Field(gt=0)
    player_id:int=Field(gt=0)
    team_salary:float|None=Field(default=None,ge=0,le=1e10)
    team_salary_source:str|None=Field(default=None,max_length=500)
    salary_basis:Literal['full-season','team-payroll','estimated']='full-season'
    salary:float|None=Field(default=None,ge=0,le=1e10)
    aav:float|None=Field(default=None,ge=0,le=1e10)
    tax_payroll:float|None=Field(default=None,ge=0,le=1e10)
    remaining_years:int|None=Field(default=None,ge=0,le=30)
    remaining_guarantee:float|None=Field(default=None,ge=0,le=1e11)
    contract_status:Literal['pre-arbitration','arbitration','free-agent','extension','other']|None=None
    salary_source:str|None=Field(default=None,max_length=500)
    contract_as_of:date|None=None
    actual_war:float|None=Field(default=None,ge=-30,le=30)
    projected_war:float|None=Field(default=None,ge=-30,le=30)
    war_provider:str|None=Field(default=None,max_length=80)
    war_source:str|None=Field(default=None,max_length=500)
    actual_as_of:date|None=None
    projection_source:str|None=Field(default=None,max_length=500)
    projection_date:date|None=None
    completed_team_games:int|None=Field(default=None,ge=0,le=200)
    total_team_games:int|None=Field(default=None,ge=1,le=200)
    actual_pa:int|None=Field(default=None,ge=0)
    projected_pa:int|None=Field(default=None,gt=0)
    actual_outs:int|None=Field(default=None,ge=0)
    projected_outs:int|None=Field(default=None,gt=0)
    notes:str|None=Field(default=None,max_length=1500)
    model_config={'extra':'forbid','allow_inf_nan':False}

    @model_validator(mode='after')
    def provenance(self):
        if self.team_salary is not None and (not self.team_salary_source or not self.contract_as_of):raise ValueError('Club salary charge needs team_salary_source and contract_as_of.')
        money=any(getattr(self,k) is not None for k in ('salary','aav','tax_payroll','remaining_guarantee','remaining_years','team_salary'))
        if money and (not self.salary_source or not self.contract_as_of):raise ValueError('Contract amounts need salary_source and contract_as_of.')
        if self.actual_war is not None and (not self.war_provider or not self.war_source or not self.actual_as_of):raise ValueError('Actual WAR needs provider, source and actual_as_of.')
        if self.projected_war is not None and (not self.war_provider or not self.projection_source or not self.projection_date):raise ValueError('Projected WAR needs the same WAR provider, projection_source and projection_date.')
        if self.actual_as_of and self.actual_as_of>TODAY():raise ValueError('Actual WAR snapshot cannot be in the future.')
        if self.projection_date and self.projection_date>TODAY():raise ValueError('Projection publication date cannot be in the future.')
        if self.projection_date and self.actual_as_of and self.projection_date>self.actual_as_of:raise ValueError('Projection must be dated on or before the actual snapshot.')
        if self.completed_team_games is not None and (not self.total_team_games or self.completed_team_games>self.total_team_games):raise ValueError('Completed games need an explicit season length and cannot exceed it.')
        if self.actual_as_of and self.actual_as_of.year!=self.season:raise ValueError('Actual WAR snapshot date must belong to the selected season.')
        if self.projection_date and self.projection_date.year>self.season:raise ValueError('Projection date is after its season.')
        if not money and self.actual_war is None and self.projected_war is None:raise ValueError('Include at least one contract amount or WAR value.')
        return self

class ImportRequest(BaseModel):
    csv_text:str=Field(max_length=1_000_000)
    commit:bool=False

def parse_csv(content):
    reader=csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    headers=reader.fieldnames or []
    if len(set(headers))!=len(headers):raise HTTPException(422,'Duplicate CSV column headers.')
    required={'season','team_id','player_id'}
    if not required.issubset(headers):raise HTTPException(422,'CSV needs season, team_id and player_id columns.')
    if set(headers)-set(InputRow.model_fields):raise HTTPException(422,'Unknown columns: '+', '.join(sorted(set(headers)-set(InputRow.model_fields))))
    records,errors,seen=[],[],set()
    for index,row in enumerate(reader,start=2):
        if index>1001:raise HTTPException(422,'Import at most 1,000 rows at a time.')
        if None in row:errors.append(f'Row {index}: extra cells; quote values containing commas.');continue
        try:
            values=InputRow.model_validate({k:v.strip() for k,v in row.items() if v is not None and v.strip()})
            key=(values.season,values.team_id,values.player_id)
            if key in seen:raise ValueError('Duplicate player/team/season in this file.')
            seen.add(key);records.append(values.model_dump(mode='json'))
        except Exception as exc:
            if hasattr(exc,'errors'):message='; '.join('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors())
            else:message=str(exc)
            errors.append(f'Row {index}: {message}')
    if errors:raise HTTPException(422,errors[:25])
    if not records:raise HTTPException(422,'The CSV contains no data rows.')
    return records

def production_metrics(v):
    actual,projected=v.get('actual_war'),v.get('projected_war')
    games,total=v.get('completed_team_games'),v.get('total_team_games')
    fraction=games/total if games is not None and total else None
    expected=projected*fraction if projected is not None and fraction is not None else None
    salary=v.get('salary')
    return dict(expected_war_to_date=expected,war_gap=actual-expected if actual is not None and expected is not None else None,
                cost_per_war=salary/actual if salary is not None and actual is not None and actual>0 and v.get('salary_basis','full-season')=='full-season' else None,
                season_fraction=fraction,
                pa_workload_ratio=v['actual_pa']/(v['projected_pa']*fraction) if v.get('actual_pa') is not None and v.get('projected_pa') and fraction else None,
                ip_workload_ratio=v['actual_outs']/(v['projected_outs']*fraction) if v.get('actual_outs') is not None and v.get('projected_outs') and fraction else None)

@router.post('/import')
def import_contracts(request:ImportRequest):
    records=parse_csv(request.csv_text)
    stamp=now();updates=0
    with Session.begin() as session:
        players={p.id:p.full_name for p in session.scalars(select(Player).where(Player.id.in_([r['player_id'] for r in records])))}
        teams={t.id:t.name for t in session.scalars(select(Team).where(Team.id.in_([r['team_id'] for r in records])))}
        bad=[r for r in records if r['player_id'] not in players or r['team_id'] not in teams]
        if bad:raise HTTPException(422,'Unknown MLB player/team IDs: '+', '.join(f"{r['player_id']}/{r['team_id']}" for r in bad[:10]))
        for r in records:
            key=(r['season'],r['team_id'],r['player_id'])
            existing=session.get(ContractRecord,key)
            updates+=bool(existing)
            if request.commit:
                if existing:existing.values=r;existing.updated_at=stamp
                else:session.add(ContractRecord(season=r['season'],team_id=r['team_id'],player_id=r['player_id'],values=r,updated_at=stamp))
        if request.commit:session.add(ContractImport(digest=hashlib.sha256(request.csv_text.encode()).hexdigest(),imported_at=stamp,records=records))
    return dict(committed=request.commit,count=len(records),replacements=updates,items=[dict(**r,full_name=players[r['player_id']],team_name=teams[r['team_id']],**production_metrics(r)) for r in records])

@router.get('/teams/{team_id}')
def team_contracts(team_id:int,season:int=Query(2026,ge=2020,le=2100)):
    from .contract_intelligence import make_pool,peer_context,quality_flags
    from .explore import schedule
    members=roster(team_id,season,'40Man')['items']
    games,_,_=schedule(season,team_id)
    by_id={p['id']:p for p in members}
    with Session() as session:
        saved=list(session.scalars(select(ContractRecord).where(ContractRecord.team_id==team_id,ContractRecord.season==season)))
        records={r.player_id:r for r in saved}
        intelligence=session.get(Payload,f'contracts:intelligence:{season}')
        intel=intelligence.payload if intelligence else {}
        info=intel.get('players',{})
        peer_records=list(session.scalars(select(ContractRecord).where(ContractRecord.season==season)))
        positions=dict(session.execute(select(Player.id,Player.primary_position)).all())
        pool=make_pool(peer_records,positions,info)
        for r in saved:
            if r.player_id not in by_id:
                person=session.get(Player,r.player_id)
                by_id[r.player_id]=dict(id=r.player_id,full_name=person.full_name if person else str(r.player_id),position=person.primary_position if person else '',age=None,batting={},pitching={})
        rows=[]
        for pid,p in by_id.items():
            record=records.get(pid);values=record.values if record else {}
            rows.append(dict(id=pid,full_name=p['full_name'],position=p['position'],age=p.get('age'),on_roster=pid in {m['id'] for m in members},mlb_pa=p.get('batting',{}).get('plateAppearances'),mlb_outs=p.get('pitching',{}).get('outs_recorded'),contract=values or None,updated_at=record.updated_at if record else None,**production_metrics(values)))
    for row in rows:
        v=row['contract'] or {};pid=row['id'];entry=info.get(str(pid),{})
        row['intelligence']=entry
        row['team_war']=intel.get('team_war',{}).get(f'{team_id}:{pid}') if v.get('war_provider')=='Baseball Reference bWAR' else None
        row['team_cost_per_war']=v['team_salary']/row['team_war'] if v.get('team_salary') is not None and row['team_war'] is not None and row['team_war']>0 and v.get('salary_basis')!='estimated' else None
        row['peer_context']=peer_context(row,pool,entry.get('season_age'),info)
        row['quality_flags']=quality_flags(row,season)
        baseline=entry.get('baseline',{})
        snapshot_day=v.get('actual_as_of')
        played=sum(g['game_type']=='R' and g['abstract_state']=='Final' and g['official_date']<=snapshot_day for g in games) if snapshot_day else None
        row['baseline_expected_to_date']=baseline['war']*min(played/162,1) if baseline.get('available') and played is not None and v.get('war_provider')=='Baseball Reference bWAR' and season in (2025,2026) else None
        row['baseline_gap']=v['actual_war']-row['baseline_expected_to_date'] if v.get('actual_war') is not None and row['baseline_expected_to_date'] is not None else None
        row['baseline_team_games']=played
    return dict(items=sorted(rows,key=lambda p:p['full_name']),season=season,team_id=team_id,roster_count=len(members),source='MLB 40-man roster plus source-tracked contract imports',note='WAR is full player-season production across all clubs. Salary basis distinguishes full-season, estimated and team payroll amounts; do not treat these as equivalent. Imported players outside the roster remain visible. USD only. No salary-based WAR expectation is inferred.')


import subprocess,sys,json,os
from pathlib import Path
from threading import Lock
_launch_lock=Lock()
_source_process=None
_source_cache=Path(__file__).resolve().parent.parent/'financial-source-cache'

def source_running():
    from .financial_sources import source_lock
    if _source_process is not None and _source_process.poll() is None:return True
    handle=source_lock(_source_cache/'refresh.lock')
    if handle is None:return True
    handle.close();return False

@router.get('/source-status')
def financial_source_status():
    with Session() as session:
        value=session.get(Payload,'contracts:public-refresh')
        data=dict(value.payload) if value else dict(records=0,note='Public financial sources have not been refreshed yet.')
    data['running']=source_running()
    log=_source_cache/'refresh.log'
    if log.exists():
        lines=log.read_text(encoding='utf-8',errors='replace').splitlines()
        if lines:
            try:data['progress']=json.loads(lines[-1])
            except ValueError:
                if not data['running']:data['last_run_error']=lines[-1][:250]
    return data

@router.post('/refresh')
def start_source_refresh():
    global _source_process
    with _launch_lock:
        if source_running():raise HTTPException(409,'A financial refresh is already running.')
        _source_cache.mkdir(parents=True,exist_ok=True)
        with (_source_cache/'refresh.log').open('w',encoding='utf-8') as output:
            _source_process=subprocess.Popen([sys.executable,'-m','app.financial_sources','--years','2025','2026','--cache-dir',str(_source_cache),'--force'],cwd=str(_source_cache.parent),stdout=output,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    return dict(running=True,note='Refreshing 2025 and 2026 payroll and published WAR. Dated projections are preserved.')

from .contract_intelligence import router as intelligence_router

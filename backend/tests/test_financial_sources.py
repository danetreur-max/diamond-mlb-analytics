import json
import pytest
from app.financial_sources import payroll_data,payroll_records,read_war,merge_source_record

def contract(years,description='2 yr',future=False,signing=16):
    return dict(contractSummary=dict(MLBAMID=1,SigningTeamID=signing,payrollType='gauranteed',ContractType='Extension',isFutureContract=future),contractYears=years,description=description)

def line(season,amount,kind='GUARANTEED',cid=1,estimate=0,team=16):
    return dict(ContractId=cid,Season=season,Salary=amount,AAV=10,TotalCBTHit=11,Type=kind,TeamId=team,isEstimate=estimate)

def test_future_contracts_join_but_options_do_not_become_guarantees():
    data=dict(dataContract=[contract([line(2026,20),line(2027,30,'CLUB OPTION')]),contract([line(2027,40,cid=2)],future=True)])
    r=payroll_records(data,16,144,2026,'2026-10-13T12:00:00+00:00')[0]
    assert r['salary']==20 and r['remaining_guarantee']==40 and r['remaining_years']==1
    assert r['salary_basis']=='full-season'

def test_estimates_and_allocated_money_are_explicit():
    d=dict(dataContract=[contract([line(2026,5)],description='prorated')])
    assert payroll_records(d,16,144,2026,'2026-10-13')[0]['salary_basis']=='team-payroll'
    d=dict(dataContract=[contract([line(2026,0,estimate=1)])])
    assert payroll_records(d,16,144,2026,'2026-10-13')[0]['salary_basis']=='estimated'

def test_historical_remaining_commitments_are_not_backdated():
    d=dict(dataContract=[contract([line(2025,5),line(2026,10)])])
    r=payroll_records(d,16,144,2025,'2026-10-13')[0]
    assert r['remaining_guarantee'] is None

def test_source_season_is_validated():
    query={'queryKey':['payroll',{'season':2026,'teamid':16}],'state':{'data':{'dataContract':[{}]}}}
    payload={'props':{'pageProps':{'dehydratedState':{'queries':[query]}}}}
    html='<script id="__NEXT_DATA__">'+json.dumps(payload)+'</script>'
    assert payroll_data(html,2026)[1]==16
    with pytest.raises(ValueError):payroll_data(html,2025)

def test_war_stints_sum_components_and_skip_totals():
    text='mlb_ID,year_ID,team_ID,stint_ID,WAR,PA,IPouts\n1,2026,ATL,1,1.2,100,\n1,2026,NYM,2,.5,50,\n1,2026,TOT,0,1.7,150,\n'
    pitch='mlb_ID,year_ID,team_ID,stint_ID,WAR,PA,IPouts\n1,2026,ATL,1,2,,90\n'
    result=read_war(dict(batting=text,pitching=pitch),{2026})[(2026,1)]
    assert result['war']==pytest.approx(3.7) and result['actual_pa']==150 and result['actual_outs']==90
    with pytest.raises(ValueError):read_war(dict(batting=text+'1,2026,ATL,1,1.2,100,\n'),{2026})

def test_refresh_preserves_projection_provider_and_invalidates_stale_pace():
    existing=dict(war_provider='fWAR',actual_war=2,projected_war=3,actual_as_of='2026-10-01',completed_team_games=160,salary=1)
    fresh=dict(war_provider='bWAR',actual_war=1,actual_as_of='2026-10-13',salary=2)
    merged=merge_source_record(existing,fresh)
    assert merged['war_provider']=='fWAR' and merged['actual_war']==2 and merged['projected_war']==3
    assert merged['salary']==2 and merged['completed_team_games']==160
    fresh['war_provider']='fWAR'
    merged=merge_source_record(existing,fresh)
    assert merged['completed_team_games'] is None

def test_refresh_lock_prevents_duplicate_processes(tmp_path):
    from app.financial_sources import source_lock
    path=tmp_path/'refresh.lock'
    first=source_lock(path)
    assert first is not None
    try:assert source_lock(path) is None
    finally:first.close()
    again=source_lock(path)
    assert again is not None
    again.close()

def test_refresh_launch_is_bounded_and_duplicate_requests_rejected(monkeypatch,tmp_path):
    import app.contracts as c
    from fastapi import HTTPException
    class Process:
        def poll(self):return None
    captured={}
    def launch(args,**kwargs):
        captured['args']=args;captured['kwargs']=kwargs;return Process()
    monkeypatch.setattr(c,'_source_cache',tmp_path/'cache')
    monkeypatch.setattr(c,'_source_process',None)
    monkeypatch.setattr(c.subprocess,'Popen',launch)
    assert c.start_source_refresh()['running']
    assert captured['args'][1:3]==['-m','app.financial_sources']
    assert '--force' in captured['args']
    with pytest.raises(HTTPException) as exc:c.start_source_refresh()
    assert exc.value.status_code==409

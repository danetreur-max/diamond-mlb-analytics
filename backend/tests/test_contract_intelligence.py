from datetime import date
import json
import pytest
from app.contract_intelligence import historical_baseline,public_projection_rows,quality_flags,availability_summary,peer_context

def test_baseline_excludes_current_and_future_years():
    r=historical_baseline({2023:1,2024:2,2025:3,2026:100,2027:200},2026)
    assert r['war']==pytest.approx(2.444,abs=.001)
    assert [x['season'] for x in r['history']]==[2025,2024,2023]
    assert historical_baseline({2025:2},2026)['limited_history']
    assert not historical_baseline({2026:99},2026)['available']

def test_public_forecast_requires_year_and_exact_identity():
    rows=[dict(xMLBAMID=1,WAR=3),dict(xMLBAMID=2,WAR=2),dict(xMLBAMID=2,WAR=4)]
    q=dict(queryKey=['/projections',dict(type='steamer',stats='bat')],state=dict(data=rows))
    data={'props':{'pageProps':{'dehydratedState':{'queries':[q]}}}}
    html='<title>2026 Projections - Steamer</title><script id="__NEXT_DATA__">'+json.dumps(data)+'</script>'
    assert list(public_projection_rows(html,2026,'bat'))==[1]
    with pytest.raises(ValueError):public_projection_rows(html,2025,'bat')

def test_quality_flags_preserve_missing_and_scope():
    r=dict(contract=dict(salary=1,salary_basis='team-payroll',actual_war=1,actual_as_of='2026-05-01',actual_pa=10,contract_as_of='2026-01-01'),mlb_pa=100,team_war=1)
    flags=quality_flags(r,2026,date(2026,10,15))
    assert 'Allocated/prorated payroll amount' in flags
    assert 'Current-year WAR snapshot over 7 days old' in flags
    assert 'MLB workload and WAR snapshot differ' in flags
    assert 'No sourced club salary charge' in flags

def test_rehab_does_not_end_injured_list_interval():
    def event(day,desc,id):return dict(id=id,date=day,effectiveDate=day,description=desc)
    events=[event('2026-04-01','Team placed Player on the 15-day injured list.',1),event('2026-04-10','Team sent Player on a rehab assignment.',2),event('2026-04-20','Team activated Player from the injured list.',3)]
    r=availability_summary(events,date(2026,3,27),date(2026,9,27))
    assert r['reported_il_days']==19 and not r['open_interval']
    assert len(r['transactions'])==3
    assert availability_summary([],date(2026,3,27),date(2026,9,27))['reported_il_days'] is None

def test_peers_require_sufficient_matched_group():
    target=dict(id=1,position='SS',contract=dict(actual_war=3,war_provider='bWAR',contract_status='arbitration'))
    info={'1':dict(workload=dict(pa=600))}
    pool=[dict(id=i,position_group='IF',provider='bWAR',status='arbitration',salary=100,war=2,age=28,work=500) for i in range(2,7)]
    assert peer_context(target,pool,27,info)['available']
    pool[0]['status']='free-agent'
    assert not peer_context(target,pool,27,info)['available']


def test_il_interval_stops_at_a_trade_out_of_the_club():
    events=[dict(id=1,date='2026-04-01',description='Team placed Player on injured list.'),dict(id=2,date='2026-04-10',description='Team traded Player.',fromTeam=dict(id=144),toTeam=dict(id=121))]
    result=availability_summary(events,date(2026,3,27),date(2026,9,27),144)
    assert result['reported_il_days']==9 and result['incomplete'] and not result['open_interval']

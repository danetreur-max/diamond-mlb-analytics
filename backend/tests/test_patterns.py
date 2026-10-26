import pytest
from app.patterns import pattern_summary

def test_batting_tradeoff_and_evidence():
    result=pattern_summary(dict(iso=.15,kPct=20,bbPct=10,plateAppearances=500),dict(iso=.20,kPct=24,bbPct=10,plateAppearances=300),'batting')
    assert result['kind']=='mixed'
    assert result['title']=='More power, more strikeouts'
    assert result['evidence'][0]['delta']==pytest.approx(.05)
    assert any('Playing time' in n for n in result['notes'])

def test_pitching_conflict():
    result=pattern_summary(dict(era=4,bbPct=7),dict(era=3,bbPct=10),'pitching')
    assert result['title']=='Lower ERA, higher walk rate'
    assert result['kind']=='mixed'

def test_missing_is_not_steady_and_zero_is_real():
    assert pattern_summary({}, {}, 'batting')['kind']=='unavailable'
    assert pattern_summary(dict(iso=0),dict(iso=.05),'batting')['title']=='Isolated power increased'
    assert pattern_summary(dict(ops=.8),dict(ops=.81),'batting')['kind']=='steady'

def test_no_claim_of_stable_missing_plate_discipline():
    result=pattern_summary(dict(iso=.10),dict(iso=.20),'batting')
    assert 'similar' not in result['title']
    assert result['notes']

def test_walk_shift_is_not_reported_as_steady():
    assert pattern_summary(dict(bbPct=5),dict(bbPct=8),'batting')['kind']=='context'
    result=pattern_summary(dict(iso=.20,kPct=25,bbPct=8),dict(iso=.15,kPct=20,bbPct=11),'batting')
    assert result['kind']=='mixed'

def test_equal_periods_require_coverage(monkeypatch):
    import app.workspace as w
    monkeypatch.setattr(w,'coverage',lambda *a:dict(complete=False))
    assert not w.period_changes(1,2026,'R','batting',5)['available']

def test_equal_periods_aggregate_complete_appearances(monkeypatch):
    import app.workspace as w
    monkeypatch.setattr(w,'coverage',lambda *a:dict(complete=True,stale=False))
    def logs(*a):
        rows=[dict(date=f'2025-04-{day:02}',stats=dict(plateAppearances=4,atBats=4,hits=day,strikeOuts=0,baseOnBalls=0,sacFlies=0),metrics=dict(plateAppearances=4)) for day in range(1,7)]
        return dict(batting=rows)
    monkeypatch.setattr(w,'local_logs',logs)
    result=w.period_changes(1,2026,'R','batting',5)
    assert result['available']
    assert result['periods'][0]['stats']['plateAppearances']==20
    assert result['periods'][0]['end']=='2025-04-05'
    assert not w.period_changes(1,2026,'R','batting',10)['available']

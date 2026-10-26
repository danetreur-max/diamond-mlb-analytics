import pytest
from app.workspace import matchup_rows

def test_matchups_prefer_total_and_preserve_handedness():
    groups=[{'splits':[
        {'split':{'code':'vl'},'stat':{'plateAppearances':10,'avg':'.300'},'team':{'id':1}},
        {'split':{'code':'vl'},'stat':{'plateAppearances':20,'avg':'.250'}},
        {'split':{'code':'vr'},'stat':{'plateAppearances':40,'avg':'.200'}},
        {'split':{'code':'home'},'stat':{'plateAppearances':999}}]}]
    result=matchup_rows(groups,'batting')
    assert len(result)==2
    assert result[0]['stats']['plateAppearances']==20
    assert result[0]['label']=='vs left-handed pitchers'
    assert result[1]['stats']['avg']==.2

def test_matchup_club_stints_are_weighted():
    rows=matchup_rows([{'splits':[
        {'split':{'code':'vl'},'team':{'id':1},'stat':{'atBats':1,'hits':1}},
        {'split':{'code':'vl'},'team':{'id':2},'stat':{'atBats':9,'hits':0}}]}],'batting')
    assert rows[0]['stats']['avg']==.1

def test_pitching_split_innings_use_outs():
    result=matchup_rows([{'splits':[{'split':{'code':'vr'},'stat':{'inningsPitched':'6.1','avg':'.222','whip':'1.20'}}]}],'pitching')
    assert result[0]['stats']['outs_recorded']==19
    assert result[0]['label']=='vs right-handed batters'

def test_qualification_applies_to_both_years(monkeypatch):
    from app import workspace
    seen=[]
    def fake(season,discipline,game_type,qualified):
        seen.append((season,qualified))
        return [],'stamp',False
    monkeypatch.setattr(workspace,'season_splits',fake)
    workspace.changes(season=2026,discipline='batting',game_type='R',metric='ops',minimum=100,direction='improved',qualified=True)
    assert seen==[(2025,True),(2026,True)]


def test_advanced_rates_and_zero_denominators():
    from app.explore import normalize_stats
    batting = normalize_stats(dict(atBats=100, plateAppearances=120, hits=30, homeRuns=5, strikeOuts=20, baseOnBalls=15, sacFlies=2, avg='.300', slg='.550'), 'batting')
    assert batting['iso'] == pytest.approx(.25)
    assert batting['babip'] == pytest.approx(25/77)
    assert batting['kPct'] == pytest.approx(100/6)
    assert batting['bbPct'] == 12.5
    pitching = normalize_stats(dict(inningsPitched='6.1', strikeOuts=10, baseOnBalls=2, homeRuns=1, battersFaced=25), 'pitching')
    assert pitching['k9'] == pytest.approx(270/19)
    assert pitching['kMinusBbPct'] == 32
    empty = normalize_stats(dict(inningsPitched='0.0', strikeOuts=0, baseOnBalls=0), 'pitching')
    assert empty['k9'] is None and empty['kbb'] is None
    assert normalize_stats({}, 'batting')['babip'] is None

def test_change_direction_depends_on_discipline():
    from app.workspace import season_changes
    def split(so):
        return [dict(player=dict(id=1,fullName='Test'),stat=dict(plateAppearances=100,battersFaced=100,strikeOuts=so,inningsPitched='30.0'))]
    assert season_changes(split(20),split(30),'kPct',0,'batting')[0]['improvement'] == -10
    assert season_changes(split(20),split(30),'kPct',0,'pitching')[0]['improvement'] == 10

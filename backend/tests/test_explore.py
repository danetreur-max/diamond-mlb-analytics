from datetime import date
import pytest
from app.explore import aggregate, history_rows, metrics, ranked_players, roster
from app import explore

def test_weighted_rates_and_baseball_innings():
    result = aggregate([{'stats':{'atBats':1,'hits':1,'homeRuns':1,'baseOnBalls':1,'plateAppearances':2}},{'stats':{'atBats':9,'hits':0,'plateAppearances':9}}], 'batting')
    assert result['avg'] == .1
    assert result['obp'] == pytest.approx(2/11)
    assert result['slg'] == .4
    assert metrics({}, 'batting')['avg'] is None
    pitching = aggregate([{'stats':{'earnedRuns':1,'hits':2,'baseOnBalls':1},'outs_recorded':17}], 'pitching')
    assert pitching['inningsPitched'] == '5.2'
    assert pitching['era'] == pytest.approx(27/17)
    assert metrics({}, 'pitching')['era'] is None

def test_traded_history_prefers_total_row_over_stints():
    rows = history_rows([{'group':{'displayName':'hitting'},'splits':[
        {'season':'2025','stat':{'atBats':10,'hits':3,'gamesPlayed':3},'team':{'name':'One'}},
        {'season':'2025','stat':{'atBats':10,'hits':2,'gamesPlayed':3},'team':{'name':'Two'}},
        {'season':'2025','stat':{'atBats':20,'hits':5,'gamesPlayed':6,'avg':'.250'}}]}])
    assert rows['batting'][0]['hits'] == 5
    assert rows['batting'][0]['gamesPlayed'] == 6
    assert rows['batting'][0]['avg'] == .25

def test_traded_history_sums_stints_with_weighted_rates():
    rows = history_rows([{'group':{'displayName':'hitting'},'splits':[
        {'season':'2025','stat':{'atBats':1,'hits':1,'gamesPlayed':1},'team':{'name':'One'}},
        {'season':'2025','stat':{'atBats':9,'hits':0,'gamesPlayed':2},'team':{'name':'Two'}}]}])
    assert rows['batting'][0]['avg'] == .1
    assert rows['batting'][0]['gamesPlayed'] == 3

def test_ranking_nulls_ties_search_and_qualification(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(explore,'Session',sessionmaker(engine))
    observed=[]
    def fake_splits(season,discipline,game_type,qualified):
        observed.append(qualified)
        return [{'player':{'id':i,'fullName':name},'team':{'id':1,'name':'Club'},'position':{'abbreviation':'P'},'stat':{'era':era,'inningsPitched':'1.0'}} for i,name,era in [(1,'Ace','1.00'),(2,'Tie','1.00'),(3,'Other','2.00'),(4,'No sample','-.--')]],'stamp',False
    monkeypatch.setattr(explore,'season_splits',fake_splits)
    result=ranked_players(season=2025,discipline='pitching',game_type='R',q='',team_id=None,position='',qualified=True,sort='era',direction='asc',offset=0,limit=50)
    assert [r['rank'] for r in result['items']] == [1,1,3,None]
    result=ranked_players(season=2025,discipline='pitching',game_type='R',q='Other',team_id=None,position='',qualified=False,sort='era',direction='asc',offset=0,limit=50)
    assert result['items'][0]['rank'] == 3
    assert observed == [True,False]

def test_historical_roster_age_and_snapshot_parameters(monkeypatch):
    seen=[]
    def fake_snapshot(path,params,**kwargs):
        seen.append(params)
        return {'roster':[{'person':{'id':99,'fullName':'Roster Player','birthDate':'2000-11-01','currentAge':99,'batSide':{'code':'L'},'pitchHand':{'code':'R'}},'position':{'abbreviation':'SS'},'status':{'description':'Active'}}]},'stamp',False
    monkeypatch.setattr(explore,'snapshot',fake_snapshot)
    monkeypatch.setattr(explore,'season_splits',lambda *args:([],'stamp',False))
    result=roster(1,season=2025,roster_type='40Man',as_of=date(2025,10,1))
    assert result['items'][0]['age'] == 24
    assert result['items'][0]['batting'] == {}
    assert seen[0]['date'] == '2025-10-01'
    assert seen[0]['rosterType'] == '40Man'

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import contracts as c
from app.db import Base,Player,Team

def test_pacing_and_nonpositive_war():
    result=c.production_metrics(dict(salary=20_000_000,actual_war=1,projected_war=3,completed_team_games=81,total_team_games=162,actual_pa=100,projected_pa=600))
    assert result['expected_war_to_date']==1.5
    assert result['war_gap']==-.5
    assert result['cost_per_war']==20_000_000
    assert result['pa_workload_ratio']==pytest.approx(1/3)
    assert c.production_metrics(dict(salary=1,actual_war=0))['cost_per_war'] is None
    assert c.production_metrics(dict(salary=1,actual_war=-1))['cost_per_war'] is None
    assert c.production_metrics(dict(actual_war=1,projected_war=3))['war_gap'] is None

def test_csv_sources_required_and_zero_salary_preserved():
    with pytest.raises(HTTPException):c.parse_csv('season,team_id,player_id,salary\n2026,144,1,100\n')
    result=c.parse_csv('season,team_id,player_id,salary,salary_source,contract_as_of\n2026,144,1,0,Test source,2026-01-01\n')
    assert result[0]['salary']==0

@pytest.mark.parametrize('csv',[
    'season,team_id,player_id,actual_war\n2026,144,1,NaN',
    'season,team_id,player_id,salary,salary_source,contract_as_of\n2026,144,1,-2,source,2026-01-01',
    'season,team_id,player_id,unknown\n2026,144,1,value',
    'season,team_id,player_id,salary,salary_source,contract_as_of\n2026,144,1,5,source,2026-01-01\n2026,144,1,6,source,2026-01-01',
    'season,team_id,player_id,salary,salary,contract_as_of\n2026,144,1,5,source,2026-01-01',
])
def test_bad_files_rejected_atomically(csv):
    with pytest.raises(HTTPException):c.parse_csv(csv)

def test_projections_dates_and_season_lengths():
    with pytest.raises(ValueError):c.InputRow(season=2026,team_id=1,player_id=1,actual_war=1,war_provider='bWAR',war_source='source',actual_as_of='2026-04-01',projected_war=3,projection_source='source',projection_date='2026-05-01')
    with pytest.raises(ValueError):c.InputRow(season=2026,team_id=1,player_id=1,salary=1,salary_source='source',contract_as_of='2026-01-01',completed_team_games=170,total_team_games=162)
    result=c.production_metrics(dict(projected_war=2,actual_war=1,completed_team_games=30,total_team_games=60))
    assert result['war_gap']==0

def test_preview_commit_and_history(monkeypatch):
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine);sessions=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(c,'Session',sessions)
    with sessions.begin() as session:
        session.add(Player(id=1,full_name='Test Player',primary_position='P'))
        session.add(Team(id=144,name='Test Team',abbreviation='TST',league='NL',division='East'))
    content='season,team_id,player_id,salary,salary_source,contract_as_of\n2026,144,1,100,source,2026-01-01'
    assert c.import_contracts(c.ImportRequest(csv_text=content))['count']==1
    with sessions() as session:assert session.get(c.ContractRecord,(2026,144,1)) is None
    assert c.import_contracts(c.ImportRequest(csv_text=content,commit=True))['committed']
    replaced=c.import_contracts(c.ImportRequest(csv_text=content.replace(',100,',',200,'),commit=True))
    assert replaced['replacements']==1
    with sessions() as session:
        assert session.get(c.ContractRecord,(2026,144,1)).values['salary']==200
        assert len(list(session.scalars(select(c.ContractImport))))==2
    with pytest.raises(HTTPException):c.import_contracts(c.ImportRequest(csv_text=content.replace('144,1,','144,999,'),commit=True))
    engine.dispose()

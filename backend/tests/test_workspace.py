from datetime import date
import pytest
from app.workspace import sample_matches, season_changes, refresh_range

def test_sample_filters_handle_missing_age_and_pitcher_roles():
    row = dict(plateAppearances=100, outs_recorded=32, age=25, gamesPlayed=10, gamesStarted=5)
    assert sample_matches(row, min_pa=100, min_ip=10, min_age=25, max_age=25, pitcher_role='starter')
    assert not sample_matches(row, min_ip=11)
    assert not sample_matches(row, pitcher_role='reliever')
    assert not sample_matches({**row, 'age':None}, min_age=20)
    assert not sample_matches({'gamesPlayed':0}, pitcher_role='reliever')

def split(pid, **stats):
    return dict(player=dict(id=pid, fullName=f'Player {pid}'),stat=stats)

def test_year_changes_use_both_samples_and_lower_era_is_improvement():
    before=[split(1,inningsPitched='10.2',era='4.00'), split(2,inningsPitched='9.2',era='5.00')]
    after=[split(1,inningsPitched='12.0',era='3.00'), split(2,inningsPitched='12.0',era='2.00'),split(3,inningsPitched='50.0',era='1.00')]
    rows=season_changes(before,after,'era',30,'pitching')
    assert len(rows)==1
    assert rows[0]['delta']==-1
    assert rows[0]['improvement']==1
    assert rows[0]['before_sample']==32

def test_year_changes_missing_rate_is_excluded_and_batting_delta_correct():
    rows=season_changes([split(1,plateAppearances=100,ops='.700'),split(2,plateAppearances=100,ops='-.---')],
                        [split(1,plateAppearances=100,ops='.850'),split(2,plateAppearances=100,ops='.800')],'ops',100,'batting')
    assert len(rows)==1
    assert rows[0]['improvement']==pytest.approx(.15)

def test_refresh_overlap_and_bounded_catchup():
    assert refresh_range('2026-10-10',date(2026,10,12))==(date(2026,10,4),date(2026,10,12))
    start,end=refresh_range('2026-01-01',date(2026,10,12))
    assert start==date(2026,1,1)
    assert (end-start).days==30
    assert refresh_range(None,date(2026,10,12))==(date(2026,10,6),date(2026,10,12))

def test_ranked_filters_apply_before_paging(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app import explore
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(explore,'Session',sessionmaker(engine))
    rows=[split(1,plateAppearances=1,age=20,ops='1.500'),split(2,plateAppearances=200,age=25,ops='.800'),split(3,plateAppearances=300,age=35,ops='.900')]
    monkeypatch.setattr(explore,'season_splits',lambda *a:(rows,'stamp',False))
    result=explore.ranked_players(season=2026,discipline='batting',game_type='R',q='',team_id=None,position='',qualified=False,sort='ops',direction='desc',offset=0,limit=1,min_pa=100,max_age=30)
    assert result['total']==1
    assert result['items'][0]['id']==2
    assert result['items'][0]['rank']==1

def test_refresh_queues_once_and_blocks_duplicates(monkeypatch):
    from sqlalchemy import create_engine,select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import HTTPException
    from app.db import Base,Run
    from app import workspace
    engine=create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(workspace,'Session',factory)
    result=workspace.refresh()
    assert result['status']=='queued'
    with pytest.raises(HTTPException) as error:
        workspace.refresh()
    assert error.value.status_code==409
    with factory() as session:
        assert len(list(session.scalars(select(Run))))==1

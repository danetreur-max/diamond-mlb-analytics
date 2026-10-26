import json
import pytest
from sqlalchemy import create_engine
from app.db import Base
from app.publish_backfill import connect, put, publish, SAVANT_EXPORTS


def database(path):
    engine = create_engine('sqlite:///' + path.as_posix())
    Base.metadata.create_all(engine)
    engine.dispose()
    with connect(path) as db:
        for tid in (1, 2):
            put(db, 'teams', dict(id=tid, name=str(tid), abbreviation=str(tid), league='', division='', updated_at='2026'))
        for pid in (1, 2):
            put(db, 'players', dict(id=pid, full_name=str(pid), primary_position='', bat_side='', throw_hand='', updated_at='2026'))


def game(db, gid, stamp, score=3, kind='R'):
    put(db, 'games', dict(id=gid, season=2026, game_type=kind, official_date='2026-01-01', scheduled_at='', status='Final', home_team_id=1, away_team_id=2, home_score=score, away_score=2, last_synced_at=stamp))
    for pid in (1, 2):
        put(db, 'player_game_batting', dict(game_id=gid, player_id=pid, team_id=pid, stats=json.dumps({'runs':score}), updated_at=stamp))
        put(db, 'player_game_pitching', dict(game_id=gid, player_id=pid, team_id=pid, outs_recorded=27, stats='{}', updated_at=stamp))
    put(db, 'source_payloads', dict(endpoint=f'/game/{gid}/boxscore', fetched_at=stamp, payload=json.dumps({'score':score})))


def test_publication_preserves_newer_primary_and_extra_games_and_remaps_runs(tmp_path):
    source, target, manifest = [tmp_path / f for f in ('source.db', 'target.db', 'plan.json')]
    database(source)
    database(target)
    exports = {}
    with connect(source) as db:
        game(db, 10, '2026-01')
        game(db, 11, '2026-02')
        for pid in (1, 2):
            for competition in ('R', 'P'):
                put(db, 'player_summary_fetches', dict(player_id=pid, competition=competition, fetched_at='2026', row_count=0))
        run = dict(id=1, start_date='2026-01-01', end_date='2026-01-31', started_at='2026', finished_at='2026', heartbeat_at='2026', status='completed', total_games=2, processed_games=2, errors='[]')
        put(db, 'ingestion_runs', run)
        for year in range(2020, 2027):
            for dataset, role in SAVANT_EXPORTS:
                key = f'{dataset}:{year}:{role}'
                exports[key] = 1
                if dataset == 'expected_statistics':
                    put(db, 'savant_season_summaries', dict(season=year,role=role,player_id=1,stats='{}',fetched_at='2026'))
                else:
                    put(db, 'savant_season_details', dict(key=key,dataset=dataset,season=year,role=role,player_id=1,stats='{}',fetched_at='2026'))
    with connect(target) as db:
        game(db, 10, '2026-03')
        game(db, 99, '2026-03', kind='S')
        put(db, 'ingestion_runs', {**run, 'start_date':'2025-01-01'})
    expected = {str(gid):dict(state='Final',season=2026,type='R',home_score=3,away_score=2) for gid in (10, 11)}
    plan = dict(status='completed',created_at='test',start_date='2020-01-01',end_date='2026-10-10',run_ids=[1],expected_games=expected,savant_completed=exports,active={'run_id':1})
    manifest.write_text(json.dumps(plan))
    assert publish(source, target, manifest)['applied'] is False
    result = publish(source, target, manifest, True)
    assert result['games_updated'] == 1
    assert __import__('pathlib').Path(result['backup']).exists()
    with connect(target) as db:
        assert db.execute('SELECT count(*) FROM games').fetchone()[0] == 3
        assert db.execute('SELECT last_synced_at FROM games WHERE id=10').fetchone()[0] == '2026-03'
        assert db.execute('SELECT start_date FROM ingestion_runs WHERE id=1').fetchone()[0] == '2025-01-01'
        assert db.execute('SELECT count(*) FROM ingestion_runs').fetchone()[0] == 2
    published = json.loads((tmp_path / 'full-backfill.json').read_text())
    assert published['run_ids'] == [2] and published['active']['run_id'] == 2
    publish(source, target, manifest, True)
    with connect(target) as db:
        assert db.execute('SELECT count(*) FROM ingestion_runs').fetchone()[0] == 2
        db.execute("UPDATE ingestion_runs SET status='running' WHERE id=1")
    with pytest.raises(ValueError, match='idle'):
        publish(source, target, manifest, True)
    with connect(target) as db:
        db.execute("UPDATE ingestion_runs SET status='completed'")
        db.execute('DELETE FROM games WHERE id=11')
        db.execute('UPDATE games SET home_score=9 WHERE id=10')
    with pytest.raises(ValueError, match='rolled back'):
        publish(source, target, manifest, True)
    with connect(target) as db:
        assert db.execute('SELECT 1 FROM games WHERE id=11').fetchone() is None
    with connect(source) as db:
        db.execute("DELETE FROM savant_season_summaries WHERE season=2020")
    with pytest.raises(ValueError, match='row-count'):
        publish(source, target, manifest, True)


def test_publication_rejects_unfinished_backfill(tmp_path):
    source, target, manifest = [tmp_path / f for f in ('source.db', 'target.db', 'plan.json')]
    database(source)
    database(target)
    manifest.write_text(json.dumps({'status':'running'}))
    with pytest.raises(ValueError, match='completed'):
        publish(source, target, manifest, True)


def test_reuse_only_audited_scope_and_preserves_newer_feature(tmp_path):
    from app.reuse_primary import reuse
    source, target, manifest = [tmp_path / f for f in ('primary.db', 'feature.db', 'plan.json')]
    database(source)
    database(target)
    with connect(source) as db:
        game(db, 10, '2026-02')
        game(db, 11, '2026-02')
        game(db, 12, '2026-02')
        db.execute('DELETE FROM player_game_pitching WHERE game_id=12')
        game(db, 99, '2026-02', kind='S')
    with connect(target) as db:
        game(db, 10, '2026-03')
    expected = {str(gid):dict(state='Final',season=2026,type='R',home_score=3,away_score=2) for gid in (10, 11, 12)}
    manifest.write_text(json.dumps({'expected_games':expected}))
    result = reuse(source, target, manifest)
    assert result == {'audited_primary_games':2,'games_copied':1,'remaining_final_games':1}
    with connect(target) as db:
        assert db.execute('SELECT count(*) FROM games').fetchone()[0] == 2
        assert db.execute('SELECT last_synced_at FROM games WHERE id=10').fetchone()[0] == '2026-03'
        assert db.execute("SELECT count(*) FROM source_payloads WHERE endpoint='/game/11/boxscore'").fetchone()[0] == 1


@pytest.mark.parametrize('status', ['running', 'interrupted', 'needs_attention'])
def test_completion_watcher_never_publishes_unfinished_ingestion(tmp_path, monkeypatch, status):
    from app import watch_publication
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'status':status}))
    monkeypatch.setattr(watch_publication, 'wait_process', lambda pid, tick: tick())
    monkeypatch.setattr(watch_publication, 'publish', lambda *a, **kw: pytest.fail('Unfinished ingestion must not publish'))
    with pytest.raises(RuntimeError, match='not publishing'):
        watch_publication.observe(1, tmp_path/'source.db', tmp_path/'target.db', manifest, tmp_path/'result.json')


def test_publication_audit_excludes_only_cancelled_final(tmp_path):
    from app.publish_backfill import coverage
    path = tmp_path / "audit.db"
    database(path)
    expected = {"1": {"state": "Final", "status": "Cancelled"},
                "2": {"state": "Final", "status": "Final", "season": 2020, "type": "R", "home_score": 1, "away_score": 0}}
    with connect(path) as db:
        assert coverage(db, expected) == [2]

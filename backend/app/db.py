import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:///./mlb.db'), **({'connect_args': {'check_same_thread': False}} if os.getenv('DATABASE_URL', 'sqlite:///./mlb.db').startswith('sqlite') else {}))
Session = sessionmaker(engine, expire_on_commit=False)

class Team(Base):
    __tablename__ = 'teams'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    abbreviation: Mapped[str]
    league: Mapped[str]
    division: Mapped[str]
    updated_at: Mapped[str] = mapped_column(default=now)

class Player(Base):
    __tablename__ = 'players'
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str]
    primary_position: Mapped[str] = mapped_column(default='')
    bat_side: Mapped[str] = mapped_column(default='')
    throw_hand: Mapped[str] = mapped_column(default='')
    updated_at: Mapped[str] = mapped_column(default=now)

class Game(Base):
    __tablename__ = 'games'
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(index=True)
    game_type: Mapped[str]
    official_date: Mapped[str] = mapped_column(index=True)
    scheduled_at: Mapped[str]
    status: Mapped[str]
    home_team_id: Mapped[int] = mapped_column(ForeignKey('teams.id'))
    away_team_id: Mapped[int] = mapped_column(ForeignKey('teams.id'))
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    last_synced_at: Mapped[str | None]

class Batting(Base):
    __tablename__ = 'player_game_batting'
    game_id: Mapped[int] = mapped_column(ForeignKey('games.id'), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey('players.id'), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey('teams.id'), primary_key=True)
    stats: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(default=now)

class Pitching(Base):
    __tablename__ = 'player_game_pitching'
    game_id: Mapped[int] = mapped_column(ForeignKey('games.id'), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey('players.id'), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey('teams.id'), primary_key=True)
    outs_recorded: Mapped[int]
    stats: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[str] = mapped_column(default=now)

class Run(Base):
    __tablename__ = 'ingestion_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    start_date: Mapped[str]
    end_date: Mapped[str]
    started_at: Mapped[str] = mapped_column(default=now)
    finished_at: Mapped[str | None]
    heartbeat_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default='queued', index=True)
    total_games: Mapped[int] = mapped_column(default=0)
    processed_games: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)

class Payload(Base):
    __tablename__ = 'source_payloads'
    endpoint: Mapped[str] = mapped_column(primary_key=True)
    fetched_at: Mapped[str] = mapped_column(default=now)
    payload: Mapped[dict] = mapped_column(JSON)

def init_db():
    Base.metadata.create_all(engine)
    migrate_game_keys(engine)


def migrate_game_keys(db_engine):
    """Preserve separate appearances when a player represents both clubs."""
    if db_engine.dialect.name != 'sqlite':
        return
    import sqlite3
    from pathlib import Path
    from sqlalchemy.schema import CreateTable
    with db_engine.connect() as connection:
        pending = []
        for cls in (Batting, Pitching):
            info = connection.exec_driver_sql(f'PRAGMA table_info({cls.__tablename__})').all()
            if info and not next(row[5] for row in info if row[1] == 'team_id'):
                pending.append(cls)
        path = connection.exec_driver_sql('PRAGMA database_list').first()[2]
    if not pending:
        return
    # Capture WAL contents before the one-time table rebuild.
    if path:
        backup = Path(path).with_name(Path(path).stem + '.before-team-key-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
    with db_engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            for cls in pending:
                name = cls.__tablename__
                temporary = name + '_team_key'
                ddl = str(CreateTable(cls.__table__).compile(db_engine)).replace('CREATE TABLE ' + name, 'CREATE TABLE ' + temporary, 1)
                connection.exec_driver_sql(ddl)
                columns = ','.join(c.name for c in cls.__table__.columns)
                connection.exec_driver_sql(f'INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {name}')
                connection.exec_driver_sql(f'DROP TABLE {name}')
                connection.exec_driver_sql(f'ALTER TABLE {temporary} RENAME TO {name}')
            if connection.exec_driver_sql('PRAGMA foreign_key_check').first():
                raise ValueError('Team appearance migration failed foreign-key verification')
            connection.commit()
        except Exception:
            connection.rollback()
            raise

def upsert(session, cls, key, values):
    obj = session.get(cls, key)
    if obj is None:
        obj = cls(**values)
        session.add(obj)
    else:
        for name, value in values.items():
            setattr(obj, name, value)
    return obj

"""Single local worker, guarded by an OS lock; queued work survives restarts."""
import os
import time
from sqlalchemy import select
from .db import Session, Run, init_db, now
from .ingest import run_import

def main():
    lock = open('.ingestion-worker.lock', 'a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    init_db()
    with Session.begin() as session:
        for run in session.scalars(select(Run).where(Run.status == 'running')):
            run.status = 'failed'
            run.finished_at = now()
            run.errors = [*run.errors, {'error': 'Worker interrupted. Retry this date range; completed games are preserved.'}]
    while True:
        with Session() as session:
            rid = session.scalar(select(Run.id).where(Run.status == 'queued').order_by(Run.id).limit(1))
        if rid is None:
            time.sleep(1)
        else:
            run_import(rid)

if __name__ == '__main__':
    main()

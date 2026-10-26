"""Wait for an existing Windows backfill process, then audit and publish its data.

This observer never restarts or interrupts ingestion. Publication errors remain
visible in its log and result file for explicit recovery.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time
from .publish_backfill import publish
from .db import now


def keep_awake(active):
    """Prevent idle system sleep for this process; leave the display unchanged."""
    if os.name != 'nt':
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetThreadExecutionState.argtypes = [wintypes.DWORD]
    kernel.SetThreadExecutionState.restype = wintypes.DWORD
    if not kernel.SetThreadExecutionState(0x80000000 | (0x00000001 if active else 0)):
        raise ctypes.WinError(ctypes.get_last_error())


def wait_process(pid, on_tick):
    if os.name != 'nt':
        raise RuntimeError('This observer uses Windows process handles')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        while True:
            on_tick()
            status = kernel.WaitForSingleObject(handle, 60000)
            if status == 0:
                return
            if status != 258:
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def observe(pid, source, target, manifest, result_path):
    def tick():
        plan = json.loads(manifest.read_text(encoding='utf-8'))
        print(json.dumps({'at':now(), 'pid':pid, 'status':plan.get('status'), 'active':plan.get('active')}), flush=True)
    wait_process(pid, tick)
    plan = json.loads(manifest.read_text(encoding='utf-8'))
    if plan.get('status') != 'completed':
        raise RuntimeError(f"Backfill process exited with manifest status {plan.get('status')}; not publishing")
    # A newly queued primary import is temporary; wait for it to finish.
    for attempt in range(180):
        try:
            result = publish(source, target, manifest, apply=True)
            result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result), flush=True)
            return
        except ValueError as exc:
            if 'queue must be idle' not in str(exc) or attempt == 179:
                raise
            print(json.dumps({'at':now(), 'status':'waiting_for_primary_queue'}), flush=True)
            time.sleep(60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--source', type=Path, default=Path('mlb.db'))
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path('full-backfill.json'))
    parser.add_argument('--result', type=Path, default=Path('full-backfill-publication.json'))
    args = parser.parse_args()
    try:
        keep_awake(True)
        observe(args.pid, args.source, args.target, args.manifest, args.result)
    except Exception as exc:
        args.result.write_text(json.dumps({'applied':False,'error':str(exc),'at':now()}, indent=2), encoding='utf-8')
        raise
    finally:
        keep_awake(False)

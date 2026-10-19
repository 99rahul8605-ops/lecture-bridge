#!/usr/bin/env python3
"""Idempotent segment acknowledgement patch for existing Railway/VPS bridge.
Only changes duplicate-segment acknowledgement in relay_api.py; no DRM changes.
"""
from pathlib import Path
from datetime import datetime, timezone
import sys

PARALLEL_OLD = '''            if index in job["inflight"] or index in job["received"]:
                return _error("Duplicate segment", 409)'''
PARALLEL_NEW = '''            if index in job["received"]:
                # An earlier POST was committed but its response may have been lost.
                # Idempotent ACK avoids corrupting or duplicating the MP4 input.
                return jsonify(received=job["received_count"], total=job["expected"],
                               already_received=True), 200
            if index in job["inflight"]:
                return _error("Duplicate segment still in progress", 409)'''
SEQUENTIAL_OLD = '''            if job["status"] != "uploading" or index != job["next"]:
                return _error("Unexpected segment index or job already closed", 409)'''
SEQUENTIAL_NEW = '''            if job["status"] != "uploading":
                return _error("Job is not uploading", 409)
            if index < job["next"]:
                # Already committed; return success if browser lost initial ACK.
                return jsonify(received=job["next"], total=job["expected"],
                               already_received=True), 200
            if index != job["next"]:
                return _error("Unexpected segment index", 409)'''

def patch_text(s):
    if PARALLEL_NEW in s or SEQUENTIAL_NEW in s:
        return s, 'already patched'
    if PARALLEL_OLD in s:
        return s.replace(PARALLEL_OLD,PARALLEL_NEW,1), 'parallel server'
    if SEQUENTIAL_OLD in s:
        return s.replace(SEQUENTIAL_OLD,SEQUENTIAL_NEW,1), 'sequential server'
    raise ValueError('Unknown relay_api.py layout; refusing to change it')

def main():
    p=Path(sys.argv[1] if len(sys.argv)>1 else 'relay_api.py')
    original=p.read_text()
    patched, variant=patch_text(original)
    compile(patched,str(p),'exec')
    if patched==original:
        print('Already patched; no file changed')
        return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    backup=p.with_name(p.name+f'.retry-backup-{stamp}')
    backup.write_text(original)
    p.write_text(patched)
    print('✅ Safe retry ACK installed ('+variant+')')
    print('Backup:', backup)
    print('⚠️ Reload Gunicorn only after active video uploads finish')
if __name__=='__main__':main()

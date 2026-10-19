#!/usr/bin/env python3
"""Idempotent local installer: progress cleanup + cloud queue registration + cloud key.

Called by setup_deploy.sh after tests and idle confirmation. Nothing deployed
remotely without the user's own git pull / local terminal action.
"""
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sys

from install_progress_cleanup import patch_text

ROOT = Path(__file__).resolve().parent


def app_patch(source):
    if 'install_cloud_routes(app)' in source:
        return source
    anchor='install_relay_routes(app)'
    if source.count(anchor)!=1 or 'from relay_api import install_relay_routes' not in source:
        raise ValueError('Unrecognized app.py; no changes applied')
    result=source.replace(anchor,anchor+'\n\nfrom cloud_queue import install_cloud_routes\ninstall_cloud_routes(app)',1)
    ast.parse(result)
    return result


def install(root, build_mode=False):
    app=root/'app.py';relay=root/'relay_api.py'
    if not app.exists() or not relay.exists() or not (root/'cloud_queue.py').exists():
        raise RuntimeError('Required app.py, relay_api.py or cloud_queue.py missing; stop')
    originals={app:app.read_text(),relay:relay.read_text()}
    proposed={app:app_patch(originals[app]),relay:patch_text(originals[relay])}
    for path,content in proposed.items():ast.parse(content)
    # Image builds patch code only; secrets must be supplied at runtime via
    # Railway Variables. Never print or bake a secret into Docker layers.
    if build_mode:
        for path,content in proposed.items():
            if content!=originals[path]:
                path.write_text(content)
                print('Docker build patched:',path.name)
        return []
    # Key file lives with queue data, not public GitHub. Save Railway env var if possible.
    queue_dir=Path(os.getenv('CLOUD_QUEUE_DIR') or (os.getenv('RELAY_DATA_DIR','/tmp/lecture_relay')+'/cloud_queue'))
    queue_dir.mkdir(parents=True,exist_ok=True)
    key_file=queue_dir/'cloud_queue.key'
    if not os.getenv('CLOUD_QUEUE_KEY') and not key_file.exists():
        fd=os.open(str(key_file),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32)+'\n')
    # Backups for both code files, before any mutation.
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    changed=[]
    try:
        for path,content in proposed.items():
            if content!=originals[path]:
                backup=path.with_name(path.name+'.before-cloud-'+stamp)
                backup.write_text(originals[path])
                path.write_text(content)
                changed.append(path)
                print('Patched:',path.name,'backup:',backup.name)
        print('Code patch complete; files changed:',len(changed))
    except Exception:
        for path in changed:path.write_text(originals[path])
        raise
    if os.getenv('CLOUD_QUEUE_KEY') and len(os.getenv('CLOUD_QUEUE_KEY'))>=24:
        print('Cloud key: using configured CLOUD_QUEUE_KEY env var (not printing secret).')
    else:
        print('\nIMPORTANT — PRIVATE Cloud Queue Key (paste only into Tampermonkey prompt, NOT GitHub):')
        print(key_file.read_text().strip() if key_file.exists() else '[no key: configure CLOUD_QUEUE_KEY]')
        print('Key file:',key_file)
    print('Railway volume recommended: mount /data; set CLOUD_QUEUE_DIR=/data/lecture_cloud')
    return changed


if __name__=='__main__':
    try: install(ROOT, build_mode=("--build-mode" in sys.argv))
    except Exception as exc:
        print('Installer aborted:',type(exc).__name__,str(exc),file=sys.stderr)
        raise SystemExit(1)

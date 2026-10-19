"""Telegram bot -> existing Railway catalog API, no bot polling conflict."""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

COMMANDS={'syncstatus','batches','batch','subjects','subject','autochapters',
          'autochapter','lectures','add','queueauto','autoclear'}


def reply(uid,command,arg=''):
    base=os.environ.get('CATALOG_BASE_URL',os.environ.get('RELAY_PUBLIC_BASE_URL','')).rstrip('/')
    key=os.environ.get('CATALOG_SYNC_KEY','')
    if not base or len(key)<24:
        return '⚠️ Set CATALOG_BASE_URL + CATALOG_SYNC_KEY in bot environment.'
    body=json.dumps({'uid':int(uid),'command':command,'arg':arg}).encode()
    req=Request(base+'/api/catalog/command',data=body,method='POST',headers={
        'X-Catalog-Key':key,'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=12) as r:data=json.load(r)
        return data.get('message') or data.get('error') or 'Empty response'
    except HTTPError as exc:
        return f'⚠️ Catalog HTTP {exc.code}. Check catalog key/server deployment.'
    except (URLError,TimeoutError,ValueError) as exc:
        return '⚠️ Catalog bridge unavailable. Check Railway URL /health.'

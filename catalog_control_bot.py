"""Owner-only Telegram control bot for existing metadata catalog bridge.

It does not download media. Start under a separate Screen session after loading .env.
"""
import asyncio
import os
import sys

from telethon import TelegramClient, events
from telethon.sessions import MemorySession

import catalog_bot_client as catalog

TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')
OWNER = os.getenv('OWNER_ID', '').strip()

if not TOKEN or not API_ID or not API_HASH or not OWNER.isdecimal():
    sys.exit('Missing BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, or numeric OWNER_ID')

owner_id = int(OWNER)
client = TelegramClient(MemorySession(), int(API_ID), API_HASH)
commands = set(catalog.COMMANDS) | {'done'}
HELP = (
    '📚 Catalog commands:\n'
    '/syncstatus, /batches, /batch 1, /subjects, /subject 1, '
    '/autochapters, /autochapter 1, /lectures, /add all, '
    '/queueauto, /done, /autoclear\n'
    'Note: /done prepares metadata; existing uploader handles MP4 separately.'
)

@client.on(events.NewMessage(incoming=True, pattern=r'^/([A-Za-z]+)(?:@\w+)?(?:\s+([\s\S]*))?$'))
async def handle(event):
    if event.sender_id != owner_id:
        return
    name = event.pattern_match.group(1).lower()
    arg = (event.pattern_match.group(2) or '').strip()
    if name in ('start', 'help'):
        await event.reply(HELP)
        return
    if name not in commands:
        return
    result = await asyncio.to_thread(catalog.reply, owner_id, name, arg)
    await event.reply(result)

if __name__ == '__main__':
    client.start(bot_token=TOKEN)
    print('✅ Catalog control bot started', flush=True)
    client.run_until_disconnected()

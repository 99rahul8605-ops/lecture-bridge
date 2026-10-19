"""Safely add catalog code to existing Railway bridge and/or Telegram bot repo.

Usage:
 python install_into_repo.py --bridge-dir /lecture-bridge --bot-dir /path/to/bot
Any absent repo path may be omitted. Existing files are backed up before changing.
"""
from __future__ import annotations
import argparse
import py_compile
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def overlay(src,dst):
    if dst.exists() and dst.read_bytes()!=src.read_bytes():
        shutil.copy2(dst,dst.with_suffix(dst.suffix+'.catalog-bak'))
    shutil.copy2(src,dst)


def patch_bridge(folder):
    target=folder/'app.py'
    if not target.exists():
        print('ERROR: app.py absent in',folder,'(no changes)');return False
    source=target.read_text(encoding='utf-8')
    if 'install_catalog_routes(app)' not in source:
        source+='\n\n# Metadata-only browser sync; existing relay remains unchanged.\nfrom catalog_web import install_catalog_routes\ninstall_catalog_routes(app)\n'
        backup=target.with_suffix('.py.catalog-bak')
        if not backup.exists():shutil.copy2(target,backup)
        target.write_text(source,encoding='utf-8')
    for fn in ('auto_catalog.py','catalog_web.py'):
        overlay(ROOT/fn,folder/fn)
    for fn in (target,folder/'auto_catalog.py',folder/'catalog_web.py'):
        py_compile.compile(str(fn),doraise=True)
    print('OK catalog server installed in',folder)
    return True


PYRO_ANCHOR='        @bot.on_message(filters.command("chapters"))'
TELE_ANCHOR='    @bot.on(events.NewMessage(pattern=r"^/chapters'
PYRO_HANDLER='''        @bot.on_message(filters.command(sorted(acc.COMMANDS)))
        async def auto_catalog_cmd(_, msg: Message):
            uid = msg.from_user.id if msg.from_user else 0
            if not OWNER_ID or uid != OWNER_ID:
                await msg.reply_text('⛔ Configure OWNER_ID to enable auto catalog.')
                return
            parts = (msg.text or '').strip().split(maxsplit=1)
            cmd = parts[0].split('@')[0].lstrip('/').lower()
            arg = parts[1] if len(parts) > 1 else ''
            result = await asyncio.to_thread(acc.reply, uid, cmd, arg)
            await msg.reply_text(result)

'''
TELE_HANDLER='''    @bot.on(events.NewMessage(pattern=r"^/(?:syncstatus|batches|batch|subjects|subject|autochapters|autochapter|lectures|add|queueauto|autoclear)(?:@\\w+)?(?:\\s|$)"))
    async def auto_catalog_cmd(event):
        uid = event.sender_id
        if not OWNER_ID or uid != OWNER_ID:
            await event.reply('⛔ Configure OWNER_ID to enable auto catalog.')
            return
        parts = (event.raw_text or '').strip().split(maxsplit=1)
        cmd = parts[0].split('@')[0].lstrip('/').lower()
        arg = parts[1] if len(parts) > 1 else ''
        result = await asyncio.to_thread(acc.reply, uid, cmd, arg)
        await event.reply(result)

'''


def patch_bot(folder):
    target=folder/'bot.py'
    if not target.exists():
        print('ERROR: bot.py absent in',folder,'(no changes)');return False
    src=target.read_text(encoding='utf-8')
    if 'import catalog_bot_client as acc' not in src:
        if 'import chapter_queue as cq' not in src:
            print('ERROR: older/different bot.py. Cannot patch automatically; no changes.');return False
        src=src.replace('import chapter_queue as cq','import chapter_queue as cq\nimport catalog_bot_client as acc',1)
    if 'async def auto_catalog_cmd(_, msg: Message):' not in src:
        if PYRO_ANCHOR not in src:
            print('ERROR: Pyrogram anchor absent; no changes.');return False
        src=src.replace(PYRO_ANCHOR,PYRO_HANDLER+PYRO_ANCHOR,1)
    if 'async def auto_catalog_cmd(event):' not in src:
        if TELE_ANCHOR not in src:
            print('ERROR: Telethon anchor absent; no changes.');return False
        src=src.replace(TELE_ANCHOR,TELE_HANDLER+TELE_ANCHOR,1)
    # Existing /done remains intact for old TXT catalogs. New pending catalog items
    # are prepared by /done, but protected videos are NEVER fetched/decrypted.
    old='''            uid = msg.from_user.id if msg.from_user else 0
            entries = cq.peek(uid)'''
    new='''            uid = msg.from_user.id if msg.from_user else 0
            if OWNER_ID and uid == OWNER_ID and await asyncio.to_thread(acc.reply,uid,'autopending') == 'yes':
                await msg.reply_text(await asyncio.to_thread(acc.reply,uid,'done'))
                return
            entries = cq.peek(uid)'''
    if "acc.reply,uid,'autopending'" not in src:
        if old not in src:
            print('ERROR: Pyrogram /done anchor absent; no changes.');return False
        src=src.replace(old,new,1)
        teleold='''        uid = event.sender_id
        entries = cq.peek(uid)'''
        telenew='''        uid = event.sender_id
        if OWNER_ID and uid == OWNER_ID and await asyncio.to_thread(acc.reply,uid,'autopending') == 'yes':
            await event.reply(await asyncio.to_thread(acc.reply,uid,'done'))
            return
        entries = cq.peek(uid)'''
        if teleold not in src:
            print('ERROR: Telethon /done anchor absent; no changes.');return False
        src=src.replace(teleold,telenew,1)
    if src != target.read_text(encoding='utf-8'):
        backup=target.with_suffix('.py.catalog-bak')
        if not backup.exists():shutil.copy2(target,backup)
        target.write_text(src,encoding='utf-8')
    overlay(ROOT/'catalog_bot_client.py',folder/'catalog_bot_client.py')
    py_compile.compile(str(target),doraise=True)
    py_compile.compile(str(folder/'catalog_bot_client.py'),doraise=True)
    print('OK Telegram commands installed in',folder)
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bridge-dir',type=Path)
    parser.add_argument('--bot-dir',type=Path)
    args=parser.parse_args()
    if not args.bridge_dir and not args.bot_dir:
        parser.error('Use --bridge-dir and/or --bot-dir')
    if args.bridge_dir: patch_bridge(args.bridge_dir.resolve())
    if args.bot_dir: patch_bot(args.bot_dir.resolve())


if __name__=='__main__':main()

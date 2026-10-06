import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from db import Database
from posts import send_today_posts

class MembersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name)/'bot.db'))
        await self.db.migrate()
        self.c = await self.db.create_client('Client','client',None)
        await self.db.bind_client(self.c['invite_code'], 101, 'Owner')
        await self.db.bind_client(self.c['invite_code'], 102, 'Assistant')
        await self.db.update_client_links(self.c['id'], sheet_url='test')
        self.c = await self.db.get_client(self.c['id'])
        self.settings=SimpleNamespace(tz=ZoneInfo('Europe/Moscow'),work_group_id=None)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_members_and_migration(self):
        await self.db.migrate()
        self.assertEqual(len(await self.db.list_members(self.c['id'])),2)
        self.assertEqual((await self.db.get_client_by_tg(102))['id'], self.c['id'])
        self.assertEqual((await self.db.get_client(self.c['id']))['telegram_id'],101)
        await self.db.set_responsible(self.c['id'],102)
        self.assertEqual((await self.db.get_client(self.c['id']))['telegram_id'],101)
        other=await self.db.create_client('Other','other',None)
        with self.assertRaises(ValueError): await self.db.bind_client(other['invite_code'],102)
        with self.assertRaises(ValueError): await self.db.remove_member(self.c['id'],101)
        await self.db.set_responsible(self.c['id'],101)
        await self.db.remove_member(self.c['id'],102)
        self.assertIsNone(await self.db.get_client_by_tg(102))

    async def test_old_owner_migrates(self):
        async with self.db.connect() as conn:
            await conn.execute('DELETE FROM client_members')
            await conn.commit()
        await self.db.migrate()
        self.assertEqual([m['telegram_id'] for m in await self.db.list_members(self.c['id'])],[101])

    async def test_delivery_modes_failure_retry(self):
        class Sheets:
            async def read_posts(self,*args):
                return [{'text':'A < B & C','time':'09:00','source_row':2},{'text':'Second','time':'10:00','source_row':3}]
        class Bot:
            def __init__(self): self.sent=[]; self.fail=102
            async def send_message(self,uid,text,**kw):
                if uid==self.fail: raise RuntimeError('blocked')
                self.sent.append((uid,text))
        bot=Bot(); sheets=Sheets()
        ok,_=await send_today_posts(bot,self.db,sheets,self.settings,self.c)
        self.assertTrue(ok)
        self.assertEqual({x[0] for x in bot.sent},{101})
        await self.db.set_posts_audience(self.c['id'],'all')
        ok,_=await send_today_posts(bot,self.db,sheets,self.settings,self.c)
        self.assertFalse(ok)
        self.assertEqual(len(bot.sent),3)
        bot.fail=None
        ok,_=await send_today_posts(bot,self.db,sheets,self.settings,self.c)
        self.assertTrue(ok)
        self.assertEqual(len(bot.sent),6)
        ok,_=await send_today_posts(bot,self.db,sheets,self.settings,self.c)
        self.assertFalse(ok)
        self.assertEqual(len(bot.sent),6)
        await send_today_posts(bot,self.db,sheets,self.settings,self.c,force=True)
        self.assertEqual(len(bot.sent),12)

    async def test_responsible_and_document_guard(self):
        from unittest.mock import AsyncMock
        import client_handlers as handlers
        handlers.configure(self.db, SimpleNamespace(admin_id=999), None)
        await self.db.set_responsible(self.c['id'], 102)
        c = await self.db.get_client(self.c['id'])
        self.assertEqual(c['responsible_id'],102)
        self.assertEqual(c['telegram_id'],101)
        guard = handlers.MemberDocumentGuard()
        event = SimpleNamespace(from_user=SimpleNamespace(id=102),data='contract_accept',answer=AsyncMock())
        handler=AsyncMock()
        await guard(handler,event,{})
        handler.assert_not_awaited()
        event.answer.assert_awaited_once()
        event.from_user.id=101
        await guard(handler,event,{})
        handler.assert_awaited_once()
        event.from_user.id=102
        event.data='act_accept:1'
        handler.reset_mock()
        await guard(handler,event,{})
        handler.assert_not_awaited()

if __name__=='__main__': unittest.main()

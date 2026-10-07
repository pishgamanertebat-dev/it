"""Offline Bale name regression coverage using real routing, hooks, storage and sidebar API."""
import asyncio
import copy
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path(os.environ.get('KOMATSO_TEST_HERMES_ROOT', r'C:\Users\win-10\AppData\Local\hermes\hermes-agent'))
sys.path.insert(0, str(TARGET))
HOOK_DIR = Path(os.environ.get('KOMATSO_TEST_HOOK_DIR', str(ROOT/'integrations/hermes/hooks/komatso-session-namer')))

class SessionNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'runtime/desktop-session-names')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'HERMES_TEST_ISOLATION': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.loop = asyncio.new_event_loop(); self.addCleanup(self.loop.close)
        for attr in ('connect', 'connect_ex'):
            p = patch.object(socket.socket, attr, Mock(side_effect=AssertionError('network prohibited')))
            p.start(); self.addCleanup(p.stop)
        p = patch.object(socket, 'getaddrinfo', Mock(side_effect=AssertionError('DNS prohibited')))
        p.start(); self.addCleanup(p.stop)
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='bale', label='Bale', adapter_factory=lambda config: None, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'bale')
        self.users = self.home/'identities.db'
        with closing(sqlite3.connect(self.users)) as db, db:
            db.execute('CREATE TABLE channel_users(platform TEXT,user_id TEXT,verified_name TEXT,display_name TEXT,registration_status TEXT,PRIMARY KEY(platform,user_id))')
            db.executemany('INSERT INTO channel_users VALUES (?,?,?,?,?)', [
                ('bale','101','حمید هدایتی','Untrusted A','approved'),
                ('bale','202','حمید هدایتی','Untrusted B','approved'),
                ('bale','303',None,'UNVERIFIED DO NOT DISPLAY','approved'),
                ('bale','404','نام لغوشده','Untrusted C','revoked'),
                ('bale','505','نام منتظر','Untrusted D','pending_approval')])
            db.execute('CREATE TABLE users(telegram_id TEXT,verified_name TEXT,telegram_display_name TEXT,registration_status TEXT)')
            db.execute("INSERT INTO users VALUES ('606','Telegram Verified','Untrusted','approved')")
        from gateway.hooks import _load_hook_dir
        self.loaded = _load_hook_dir(HOOK_DIR)
        self.handler = self.loaded[2]
        sys.modules[self.handler.__module__].USERS_DB = self.users

    def test_routing_title_history_and_desktop_contract(self):
        from gateway.config import GatewayConfig, Platform
        from gateway.session import SessionStore, SessionSource, build_session_key
        from gateway.platforms.base import MessageEvent, MessageType
        from hermes_state import SessionDB
        from hermes_cli.web_routers import profiles
        from agent.title_generator import apply_instant_title
        store = SessionStore(self.home/'sessions', GatewayConfig())
        self.addCleanup(store._db_handle_cache.close_all, lambda db: db.close())
        db = SessionDB(self.home/'state.db'); self.addCleanup(db.close)
        identity_before = self.users.read_bytes()
        cases = [ ('101', 'ریتارد دستگاه ضعیف است', 'ریتارد اهرمی 465', 'حمید هدایتی'),
                  ('202', 'ریتارد دستگاه ضعیف است', 'ریتارد اهرمی 465', 'حمید هدایتی'),
                  ('303', 'سؤال بدون نام تأییدشده', '', '303'),
                  ('707', 'سؤال مدیر فاقد رکورد ثبت نام', '', '707'),
                  ('808', ('می\u200cکند 👨\u200d👩\u200d👧\u200d👦 دستگاه ضعیف است ')*80, '', '808'),
                  ('404', 'سؤال کاربر لغوشده', '', None),
                  ('505', 'سؤال منتظر تأیید', '', None) ]
        sessions = []
        for uid, text, generated, name in cases:
            with self.subTest(user=uid):
                source = SessionSource(platform=Platform('bale'), user_id=uid, chat_id=uid, chat_type='dm', user_name='same untrusted name')
                event = MessageEvent(text=text, source=source, message_type=MessageType.TEXT, channel_prompt='unchanged trusted policy')
                source_before = copy.deepcopy(source.to_dict()); event_before = event.text.encode('utf-8')
                entry = store.get_or_create_session(source)
                key_before = build_session_key(source)
                db.append_message(entry.session_id, 'user', text)
                ctx = dict(platform='bale',user_id=uid,chat_id=uid,chat_type='dm',session_id=entry.session_id,message=text[:500])
                ctx_before = copy.deepcopy(ctx)
                history_before = db.get_messages_as_conversation(entry.session_id)
                self.loop.run_until_complete(self.handler('agent:start',ctx))
                self.assertIsNone(db.get_session_title(entry.session_id))
                if generated:
                    db.set_auto_title(entry.session_id, generated, source='llm')
                elif uid == '808':
                    apply_instant_title(db,entry.session_id,text)
                self.loop.run_until_complete(self.handler('agent:end',ctx))
                title = db.get_session_title(entry.session_id)
                if name:
                    self.assertTrue(title.startswith('بله | '+name+' — '), title)
                    self.assertLessEqual(len(title),db.MAX_TITLE_LENGTH)
                    if generated: self.assertIn(generated,title)
                    if uid == '808': self.assertIn('👨\u200d👩\u200d👧\u200d👦',title); self.assertIn('می\u200cکند',title)
                    self.loop.run_until_complete(self.handler('agent:end',ctx))
                    self.assertEqual(db.get_session_title(entry.session_id), title)
                    self.assertNotIn('UNVERIFIED',title)
                else: self.assertIsNone(title)
                self.assertEqual(ctx,ctx_before)
                self.assertEqual(event.text.encode('utf-8'),event_before)
                self.assertEqual(event.channel_prompt,'unchanged trusted policy')
                self.assertEqual(source.to_dict(),source_before)
                self.assertEqual(build_session_key(source),key_before)
                self.assertEqual(db.get_messages_as_conversation(entry.session_id),history_before)
                self.assertEqual(db.get_session(entry.session_id)['user_id'],uid)
                sessions.append(entry)
        self.assertNotEqual(sessions[0].session_key,sessions[1].session_key)
        self.assertNotEqual(sessions[0].session_id,sessions[1].session_id)
        self.assertNotEqual(db.get_session_title(sessions[0].session_id),db.get_session_title(sessions[1].session_id))
        # A group's identity is never reassigned to its last sender.
        db.create_session('group-fixture','bale')
        for kind,chat in [('group','group-900'),('dm','999')]:
            self.loop.run_until_complete(self.handler('agent:end',dict(platform='bale',user_id='101',chat_id=chat,chat_type=kind,session_id='group-fixture',message='group question')))
            self.assertIsNone(db.get_session_title('group-fixture'))
        non_bale=SessionSource(platform=Platform.TELEGRAM,user_id='606',chat_id='606',chat_type='dm')
        entry=store.get_or_create_session(non_bale)
        self.loop.run_until_complete(self.handler('agent:start',dict(platform='telegram',user_id='606',session_id=entry.session_id)))
        self.assertEqual(db.get_session_title(entry.session_id),'تلگرام | Telegram Verified')
        db.create_session('non-bale-slack','slack')
        self.loop.run_until_complete(self.handler('agent:end',dict(platform='slack',user_id='101',session_id='non-bale-slack')))
        self.assertIsNone(db.get_session_title('non-bale-slack'))
        with patch.object(profiles,'_profile_targets',return_value=[('default',self.home)]):
            output=profiles.get_profiles_sessions_sidebar.__wrapped__()
        rows={r['id']:r for r in output['messaging']['sessions']}
        self.assertFalse(output['errors'])
        for entry in sessions:
            self.assertEqual(rows[entry.session_id]['title'],db.get_session_title(entry.session_id))
            self.assertNotIn('system_prompt',rows[entry.session_id])
        self.assertEqual(self.users.read_bytes(),identity_before)
        print('SAFE SMOKE:',rows[sessions[0].session_id]['title'])

    def test_profile_scoped_hook_provisioning_and_cache(self):
        from gateway.hooks import ProfileHookRegistries
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from hermes_state import SessionDB
        from hermes_cli.web_routers import profiles
        homes=[self.home/'A',self.home/'B']
        hook_dir=HOOK_DIR
        unprovisioned = ProfileHookRegistries()
        token = set_hermes_home_override(homes[1])
        try:
            self.assertEqual(unprovisioned.loaded_hooks, [])
        finally: reset_hermes_home_override(token)
        for home in homes:
            shutil.copytree(hook_dir,home/'hooks/komatso-session-namer',ignore=shutil.ignore_patterns('__pycache__'))
            db=SessionDB(home/'state.db')
            db.create_session('profile-fixture','bale',user_id='101',session_key='agent:maintenance:bale:dm:101')
            db.append_message('profile-fixture','user','سؤال اول')
            db.set_auto_title('profile-fixture','عنوان موجود',source='llm'); db.close()
        registry=ProfileHookRegistries()
        for home in [homes[0],homes[1],homes[0]]:
            token=set_hermes_home_override(home)
            try:
                active=registry._active()
                for handler in active._handlers['agent:end']:
                    sys.modules[handler.__module__].USERS_DB=self.users
                ctx=dict(platform='bale',user_id='101',chat_id='101',chat_type='dm',session_id='profile-fixture',message='سؤال')
                self.loop.run_until_complete(registry.emit('agent:end',ctx))
                db=SessionDB(home/'state.db')
                self.assertEqual(db.get_session_title('profile-fixture'),'بله | حمید هدایتی — عنوان موجود'); db.close()
            finally: reset_hermes_home_override(token)
        with patch.object(profiles,'_profile_targets',return_value=[('A',homes[0]),('B',homes[1])]):
            first=profiles.get_profiles_sessions_sidebar.__wrapped__()
            db=SessionDB(homes[1]/'state.db'); db.set_session_title('profile-fixture','بله | حمید هدایتی — عنوان دوم'); db.close()
            second=profiles.get_profiles_sessions_sidebar.__wrapped__()
        before={r['profile']:r['title'] for r in first['messaging']['sessions']}
        after={r['profile']:r['title'] for r in second['messaging']['sessions']}
        self.assertEqual(before['A'],after['A']); self.assertNotEqual(before['B'],after['B'])

if __name__ == '__main__': unittest.main(verbosity=2)

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tools.fleet.work_orders.channels.bale.message_handler import WorkOrderMenuHandler
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class BaleMessageHandlerTests(PermissionDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.handler = WorkOrderMenuHandler(db_path=self.db_path, clock=lambda: self.now)
        def start(key, session, request, gateway, send):
            self.assertEqual(request['action'], 'propose')
            session.stage = 'BUSY'
        self.handler._start_request = start
        self.replies = []

    def message(self, text, user_id="455740857", chat_id="455740857", platform="bale", chat_type="dm"):
        event = SimpleNamespace(text=text, source=SimpleNamespace(user_id=user_id, chat_id=chat_id, platform=platform, chat_type=chat_type))
        return self.handler.handle(event, None, send=lambda gateway, chat, reply: self.replies.append((chat, reply)))

    def test_entry_and_selection_reply_to_originating_chat(self):
        self.assertEqual(self.message("حکم کار")["action"], "skip")
        self.assertIn("1) هواکش", self.replies[-1][1])
        self.assertEqual(self.replies[-1][0], "455740857")
        self.assertEqual(self.message("۱")["reason"], "work-order-type-selected")
        self.assertIn("تهیهٔ پیشنهاد", self.replies[-1][1])
        self.assertEqual(next(iter(self.handler.pending.values())).stage, "BUSY")

    def test_unknown_user_is_denied_and_does_not_fall_through(self):
        self.assertEqual(self.message("حکم کار", user_id="9999")["reason"], "work-order-permission-denied")
        self.assertEqual(self.handler.pending, {})

    def test_chat_id_cannot_substitute_for_sender_identity(self):
        self.assertEqual(self.message("حکم کار", user_id=None)["reason"], "work-order-permission-denied")

    def test_oil_type_requests_automatic_proposal(self):
        self.message("حکم کار")
        self.assertEqual(self.message("۲")["reason"], "work-order-type-selected")
        self.assertEqual(next(iter(self.handler.pending.values())).stage, 'BUSY')
        self.assertIn('پیشنهاد تعویض روغن', self.replies[-1][1])

    def test_unrelated_channels_and_groups_are_untouched(self):
        self.assertIsNone(self.message("حکم کار", platform="telegram"))
        self.assertIsNone(self.message("حکم کار", chat_type="group"))
        self.assertEqual(self.replies, [])

    def test_session_is_isolated_by_user_and_chat(self):
        self.message("حکم کار")
        self.assertIsNone(self.message("۱", user_id="9999"))
        self.assertIsNone(self.message("۱", chat_id="other-chat"))
        self.assertEqual(len(self.replies), 1)

    def test_menu_expires_and_unrelated_chat_exits(self):
        self.message("حکم کار")
        self.now += 601
        self.assertIsNone(self.message("۱"))
        self.message("حکم کار")
        self.assertIsNone(self.message("سلام"))
        self.assertIsNone(self.message("۱"))

    def test_cancel_and_invalid_number(self):
        self.message("حکم کار")
        self.assertEqual(self.message("۹")["reason"], "work-order-selection-rejected")
        self.assertEqual(self.message("انصراف")["reason"], "work-order-menu-cancelled")

    def test_revocation_blocks_existing_menu(self):
        self.message("حکم کار")
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.assertEqual(self.message("۱")["reason"], "work-order-permission-denied")

    def test_failure_is_handled_without_ai_fallback(self):
        with patch("tools.fleet.work_orders.channels.bale.message_handler.build_work_order_menu", side_effect=RuntimeError("TEST")), self.assertLogs("tools.fleet.work_orders.channels.bale.message_handler", level="ERROR"):
            self.assertEqual(self.message("حکم کار")["reason"], "work-order-menu-error")

    def test_arabic_letters_and_half_space_are_normalized(self):
        for text in ("حكم كار", "حکم‌کار", "  حکم   کار "):
            with self.subTest(text=text):
                self.assertEqual(self.message(text)["reason"], "work-order-menu")

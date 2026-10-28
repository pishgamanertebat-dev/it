"""Trusted-actor prompt isolation; real retrieval is verified by the live harness."""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
CORE = HOME/'hermes-agent'
sys.path[:0] = [str(ROOT), str(CORE), str(CORE/'venv/Lib/site-packages')]
import tools
if str(CORE/'tools') not in tools.__path__:
    tools.__path__.append(str(CORE/'tools'))

from integrations.hermes.admin_parts_policy import admin_parts_policy, EMAMI_PREFERENCE
from integrations.hermes.role_routing import AuthorizationStore
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from gateway.session_context import set_session_vars, clear_session_vars


class AdminPartsPolicyTests(unittest.TestCase):
    @contextmanager
    def actor(self, user='', *, profile='admin', platform='bale', chat=None, kind='dm'):
        home = HOME if profile == 'default' else HOME/'profiles'/profile
        token = set_hermes_home_override(home)
        tokens = set_session_vars(platform=platform, user_id=user, chat_id=user if chat is None else chat,
                                  chat_type=kind, profile=profile)
        try:
            yield
        finally:
            clear_session_vars(tokens)
            reset_hermes_home_override(token)

    def test_verified_production_actor_gets_preference_within_native_limit(self):
        store = AuthorizationStore()
        self.assertEqual(store.identity_label('397185913'), 'امامی')
        self.assertEqual(store.resolve_profile('397185913', '397185913', 'bale'), 'admin')
        with self.actor('397185913'):
            policy = admin_parts_policy({})
        self.assertIn(EMAMI_PREFERENCE, policy)
        self.assertLessEqual(len(policy), 4000)

    def test_other_approved_admin_gets_only_general_policy(self):
        self.assertEqual(AuthorizationStore().resolve_profile('514458396', '514458396'), 'admin')
        with self.actor('514458396'):
            policy = admin_parts_policy({'display_name': 'امامی', 'user_id': '397185913'})
        self.assertTrue(policy)
        self.assertNotIn(EMAMI_PREFERENCE, policy)

    def test_owner_display_name_and_untrusted_metadata_never_attach_preference(self):
        for user in ('455740857', '', 'unregistered-synthetic'):
            with self.subTest(user=user), self.actor(user):
                policy = admin_parts_policy({'display_name': 'امامی', 'user_id': '397185913'})
                self.assertTrue(policy)
                self.assertNotIn(EMAMI_PREFERENCE, policy)

    def test_non_bale_or_non_private_context_never_attaches_preference(self):
        for kwargs in ({'platform': 'telegram'}, {'kind': 'group'}, {'chat': 'another-chat'}):
            with self.subTest(scope=kwargs), self.actor('397185913', **kwargs):
                self.assertNotIn(EMAMI_PREFERENCE, admin_parts_policy({}))

    def test_other_profiles_receive_no_admin_policy_even_for_emami(self):
        for profile in ('default', 'maintenance', 'net'):
            with self.subTest(profile=profile), self.actor('397185913', profile=profile):
                self.assertEqual(admin_parts_policy({}), '')

    def test_actor_context_does_not_leak_to_the_next_session(self):
        with self.actor('397185913'):
            self.assertIn(EMAMI_PREFERENCE, admin_parts_policy({}))
        with self.actor('514458396'):
            self.assertNotIn(EMAMI_PREFERENCE, admin_parts_policy({}))


if __name__ == '__main__':
    unittest.main()

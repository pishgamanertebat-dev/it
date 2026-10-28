"""Admin-only source instructions using native prompt sections and trusted identity.

This selects no tools and grants no role, capability or routing permission.
"""
from pathlib import Path

from integrations.hermes.technical_docs_boundary import technical_docs_enabled

POLICY = Path(__file__).parent/'profiles/admin/PARTS_POLICY.md'
EMAMI_BALE_ID = '397185913'
EMAMI_PREFERENCE = (
    '\nVERIFIED PERSONAL SOURCE PREFERENCE: For this authenticated Bale identity, '
    'check the related Parts Book by default with strong priority for machinery '
    'part images, identification, numbers, bases/brackets/fittings, components '
    'and assembly structure, even without an explicit Parts Book request. '
    'When a relevant verified figure helps, request render=true and include it '
    'by default even for a number/explanation-only question. An explicit request '
    'for a different output (including text-only or real/installed photos) wins. '
    'Troubleshooting still starts with Shop Manual. This is a source preference '
    'only; it grants no permissions and does not apply to other identities.'
)


def admin_parts_policy(info):
    from hermes_constants import get_hermes_home
    from gateway.session_context import get_session_env

    home = get_hermes_home().resolve()
    if (home.name != 'admin' or home.parent.name != 'profiles'
            or not technical_docs_enabled(home)):
        return ''
    policy = POLICY.read_text(encoding='utf-8')
    platform = get_session_env('HERMES_SESSION_PLATFORM', '')
    user = get_session_env('HERMES_SESSION_USER_ID', '')
    chat = get_session_env('HERMES_SESSION_CHAT_ID', '')
    private = get_session_env('HERMES_SESSION_CHAT_TYPE', '') == 'dm'
    if platform == 'bale' and private and user == chat == EMAMI_BALE_ID:
        from integrations.hermes.role_routing import AuthorizationStore
        # Fresh approved/private role mapping; display names and message text
        # never attach a preference. Failure leaves only the general policy.
        if AuthorizationStore().resolve_profile(user, chat, platform) == 'admin':
            policy += EMAMI_PREFERENCE
    return policy

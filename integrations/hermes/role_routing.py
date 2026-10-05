"""Project authorization access without shadowing Hermes' tools package."""
from pathlib import Path
import sys
ROOT=Path('E:/KomatsoAI')
if str(ROOT) not in sys.path:sys.path.append(str(ROOT))
import tools
project_tools=str(ROOT/'tools')
if project_tools not in tools.__path__:tools.__path__.append(project_tools)
from tools.authorization import AuthorizationStore, FUNCTION_READ

def resolve_profile(*,platform,user_id,chat_id,**scope):
    # A role cannot reroute an organization group to a private business profile.
    if scope.get('guild_id') or scope.get('thread_id') or scope.get('parent_chat_id'):
        return None
    return AuthorizationStore().resolve_profile(user_id,chat_id,platform)


def resolve_toolsets(*, platform, user_id, chat_id, chat_type, base_toolsets):
    """Add business reads by capability to any configured expert profile."""
    base=[x for x in base_toolsets if x!='komatso_function']
    if (platform=='bale' and chat_type=='dm' and user_id and str(user_id)==str(chat_id)
            and AuthorizationStore().function_scope(user_id,platform)):
        base.append('komatso_function')
    return base

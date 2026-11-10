"""Retain the original occurrence membership while rechecking current permission."""
from tools.authorization.store import RecipientSetResolution


class OccurrenceAuthorization:
    def __init__(self,store,capability,recipients):
        self.store=store;self.capability=capability;self.recipients=frozenset(recipients)
    def __getattr__(self,name):return getattr(self.store,name)
    def resolve_active_recipients(self,capability):
        result=self.store.resolve_active_recipients(capability)
        if capability!=self.capability or result.status!='ready':return result
        users=tuple(u for u in result.recipients if str(u) in self.recipients)
        return RecipientSetResolution('ready' if users else 'no_eligible_recipient',users,
            len(result.recipients),len(result.recipients)-len(users))

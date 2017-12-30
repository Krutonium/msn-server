from typing import Dict

_session_clearing: {} # type: Dict[str, str]

class YahooSessionClearing:
    def __init__(self, id, user):
        self.user = user
        _session_clearing[id] = user
    
    def pop_session(self, id):
        if _session_clearing[id] == self.user:
            del _session_clearing[id]
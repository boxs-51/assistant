import threading
import uuid

class SessionManager:
    def __init__(self):
        self._sessions = {}
        self._session_locks = {}
        self._sessions_lock = threading.RLock()
        self._default_conversation_id = f"bridge:{uuid.uuid4().hex}"

    def get_or_create(self, conversation_id: str):
        cid = str(conversation_id).strip() if conversation_id else self._default_conversation_id
        if len(cid) > 256: raise ValueError("conversation_id quá dài")
        
        with self._sessions_lock:
            if cid not in self._sessions:
                from ..schemas.context import AgentContextSession
                self._sessions[cid] = AgentContextSession()
                self._session_locks[cid] = threading.Lock()
            return cid, self._sessions[cid], self._session_locks[cid]
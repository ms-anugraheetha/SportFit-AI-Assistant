"""Server-side conversation state.

State lives in memory (single process) with a TTL: restarting the server forgets conversations,
which is acceptable for a demo and documented as a limitation. Because the server - not the model
and not the browser - owns the state, a crafted message cannot jump to "confirmed".
"""

import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_CONVERSATIONS = 2000
MAX_TURNS = 80


class ConvState(str, Enum):
    general = "general"
    collecting_repair_details = "collecting_repair_details"
    awaiting_submission_confirmation = "awaiting_submission_confirmation"
    request_created = "request_created"
    tracking_request = "tracking_request"
    handoff = "handoff"


@dataclass
class Conversation:
    id: str
    state: ConvState = ConvState.general
    draft: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    asking: str | None = None
    bad_attempts: int = 0
    last_reference: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)  # general Q&A only; never form data
    turns: int = 0
    last_active: float = field(default_factory=time.monotonic)

    def reset_form(self) -> None:
        self.draft.clear()
        self.skipped.clear()
        self.asking = None
        self.bad_attempts = 0


class ConversationStore:
    def __init__(self, ttl_seconds: int = 1800, max_items: int = MAX_CONVERSATIONS) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: dict[str, Conversation] = {}

    def clear(self) -> None:
        self._items.clear()

    def _gc(self) -> None:
        now = time.monotonic()
        for cid in [c for c, v in self._items.items() if now - v.last_active > self._ttl]:
            del self._items[cid]
        if len(self._items) >= self._max:  # drop the least recently used
            for cid, _ in sorted(self._items.items(), key=lambda kv: kv[1].last_active)[: self._max // 10 or 1]:
                del self._items[cid]

    def get_or_create(self, conversation_id: str | None) -> Conversation:
        """Unknown, expired or malformed ids silently start a fresh conversation in the general state."""
        self._gc()
        if conversation_id and _ID_RE.match(conversation_id):
            existing = self._items.get(conversation_id)
            if existing is not None:
                existing.last_active = time.monotonic()
                return existing
        conv = Conversation(id=secrets.token_urlsafe(18))
        self._items[conv.id] = conv
        return conv

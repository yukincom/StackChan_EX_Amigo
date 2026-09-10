"""In-memory event sessions for the device continuous-conversation route.

The device owns microphone capture and playback.  This module only validates
the small JSON event contract, keeps per-session transport state, and makes an
event retry idempotent.  It deliberately does not persist transcripts.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import threading
import time
import uuid
from typing import Callable


SCHEMA_VERSION = 1
MAX_BODY_BYTES = 8 * 1024
MAX_ID_CHARS = 64
MAX_TEXT_CHARS = 1000
MAX_SESSIONS = 8
MAX_EVENTS_PER_SESSION = 32
EVENT_TYPES = {"start", "stop", "face_touch", "speech", "playback_completed"}


class EventContractError(ValueError):
    """A request cannot be interpreted as a conversation event."""


class EventConflictError(RuntimeError):
    """A valid event conflicts with the current session state."""


@dataclass
class _CachedEvent:
    fingerprint: str
    response: dict


@dataclass
class _Session:
    closed: bool = False
    accept_next: bool = True
    last_seen: float = field(default_factory=time.monotonic)
    pending_reply_id: str | None = None
    events: OrderedDict[str, _CachedEvent] = field(default_factory=OrderedDict)


def _required_id(payload: dict, key: str) -> str:
    value = payload.get(key)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_ID_CHARS
        or any(character in value for character in "\r\n\x00")
    ):
        raise EventContractError(f"invalid {key}")
    return value


def _fingerprint(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ConversationEventStore:
    """Small serialized session store intended for one local Stack-chan host."""

    def __init__(self, *, max_sessions: int = MAX_SESSIONS) -> None:
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        self._max_sessions = max_sessions
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _evict_if_needed(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            oldest_id = min(self._sessions, key=lambda key: self._sessions[key].last_seen)
            del self._sessions[oldest_id]

    @staticmethod
    def _response(session_id: str, event_id: str, *, state: str, action: str,
                  reply: dict | None = None, reason: str = "") -> dict:
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "event_id": event_id,
            "state": state,
            "action": action,
            "reply": reply,
            "reason": reason,
        }

    @staticmethod
    def _remember(session: _Session, event_id: str, fingerprint: str, response: dict) -> None:
        session.events[event_id] = _CachedEvent(fingerprint, response)
        session.events.move_to_end(event_id)
        while len(session.events) > MAX_EVENTS_PER_SESSION:
            session.events.popitem(last=False)

    def handle(self, payload: dict, reply_builder: Callable[[str], dict]) -> dict:
        if not isinstance(payload, dict):
            raise EventContractError("JSON object required")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise EventContractError("unsupported schema_version")

        session_id = _required_id(payload, "session_id")
        event_id = _required_id(payload, "event_id")
        event_type = payload.get("type")
        if event_type not in EVENT_TYPES:
            raise EventContractError("invalid type")
        if event_type == "speech":
            allowed_keys = {"schema_version", "session_id", "event_id", "type", "text"}
        elif event_type == "playback_completed":
            allowed_keys = {"schema_version", "session_id", "event_id", "type", "reply_id"}
        else:
            allowed_keys = {"schema_version", "session_id", "event_id", "type"}
        if set(payload) != allowed_keys:
            raise EventContractError("unexpected or missing event fields")

        fingerprint = _fingerprint(payload)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and event_id in session.events:
                cached = session.events[event_id]
                if cached.fingerprint != fingerprint:
                    raise EventConflictError("event_id already used with different content")
                session.last_seen = time.monotonic()
                return cached.response

            if event_type == "start":
                if session is None:
                    self._evict_if_needed()
                    session = _Session()
                    self._sessions[session_id] = session
                else:
                    session.closed = False
                    session.accept_next = True
                    session.pending_reply_id = None
                    session.events.clear()
                response = self._response(
                    session_id, event_id, state="active", action="listen", reason="started",
                )
            else:
                if session is None or session.closed:
                    raise EventConflictError("session_not_started")

                if event_type == "face_touch":
                    session.accept_next = True
                    response = self._response(
                        session_id, event_id, state="active", action="listen", reason="face_touch",
                    )
                elif event_type == "stop":
                    session.closed = True
                    session.pending_reply_id = None
                    response = self._response(
                        session_id, event_id, state="closed", action="stopped", reason="stopped",
                    )
                elif event_type == "playback_completed":
                    reply_id = _required_id(payload, "reply_id")
                    if session.pending_reply_id != reply_id:
                        raise EventConflictError("reply_id does not match pending reply")
                    session.pending_reply_id = None
                    response = self._response(
                        session_id, event_id, state="active", action="listen",
                        reason="playback_completed",
                    )
                else:
                    text = payload.get("text")
                    if (
                        not isinstance(text, str)
                        or not text.strip()
                        or len(text) > MAX_TEXT_CHARS
                        or "\x00" in text
                    ):
                        raise EventContractError("invalid text")
                    if session.pending_reply_id is not None:
                        raise EventConflictError("reply_playback_pending")

                    was_first_input = session.accept_next
                    result = reply_builder(text.strip())
                    if not isinstance(result, dict) or not result.get("success"):
                        error = result.get("error") if isinstance(result, dict) else None
                        raise RuntimeError(str(error or "chat backend failed"))
                    reply_text = result.get("response")
                    if not isinstance(reply_text, str) or not reply_text.strip():
                        raise RuntimeError("chat backend returned an empty reply")
                    reply_id = str(uuid.uuid4())
                    session.pending_reply_id = reply_id
                    session.accept_next = False
                    response = self._response(
                        session_id,
                        event_id,
                        state="active",
                        action="reply",
                        reply={"id": reply_id, "text": reply_text},
                        reason="first_input" if was_first_input else "active_conversation",
                    )
                    stackchan_action = result.get("stackchan_action")
                    if isinstance(stackchan_action, dict):
                        response["stackchan_action"] = stackchan_action

            session.last_seen = time.monotonic()
            self._remember(session, event_id, fingerprint, response)
            return response


conversation_event_store = ConversationEventStore()

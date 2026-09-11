"""In-memory transport state for Stack-chan conversation events.

The device owns capture, playback, and the selected input mode.  This module
validates the wire contract, serializes small state transitions, and keeps
retries idempotent.  Model/search work deliberately runs without holding the
store lock, and transcripts are never persisted here.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import copy
import json
import re
import threading
import time
from typing import Callable
from urllib.parse import urlparse
import uuid


SCHEMA_VERSION = 1
ACTION_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {SCHEMA_VERSION, ACTION_SCHEMA_VERSION}
MAX_BODY_BYTES = 8 * 1024
MAX_ID_CHARS = 64
MAX_TEXT_CHARS = 1000
MAX_REPLY_CHARS = 500
MAX_SPEAKER_CHARS = 64
MAX_SPEAKER_LABEL_CHARS = 128
MAX_ANNOUNCE_ENDPOINT_CHARS = 64
MAX_AUDIO_URL_CHARS = 2048
MAX_SESSIONS = 8
MAX_EVENTS_PER_SESSION = 32
MAX_TOUCH_REQUESTS = 64

V1_EVENT_TYPES = {"start", "stop", "face_touch", "speech", "playback_completed"}
V2_EVENT_TYPES = V1_EVENT_TYPES | {"action_completed"}
ACTION_TYPES = {"camera_capture", "audio_playback"}
CAMERA_MODES = {"archive", "transient"}
CAMERA_REQUESTERS = {"user", "ai"}
COMPLETION_STATUSES = {"succeeded", "failed"}
_ANNOUNCE_ENDPOINT_RE = re.compile(r"[A-Za-z0-9_-]+")


class EventContractError(ValueError):
    """A request cannot be interpreted as a conversation event."""


class EventConflictError(RuntimeError):
    """A valid event conflicts with the current session state."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class TouchReservation:
    """Opaque token tying one touch request to its in-flight cache entry."""

    session_id: str
    event_id: str
    fingerprint: str


@dataclass
class _CachedEvent:
    fingerprint: str
    response: dict


@dataclass
class _Session:
    closed: bool = False
    accept_next: bool = True
    one_shot: bool = False
    generation: int = 1
    last_seen: float = field(default_factory=time.monotonic)
    pending_reply_id: str | None = None
    pending_action_id: str | None = None
    pending_action_type: str | None = None
    events: OrderedDict[str, _CachedEvent] = field(default_factory=OrderedDict)
    processing_events: dict[str, str] = field(default_factory=dict)


@dataclass
class _TouchRequest:
    fingerprint: str
    processing: bool = True
    response: dict | None = None


def validate_wire_id(value: object, key: str) -> str:
    """Validate one opaque device identifier and return it unchanged."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_ID_CHARS
        or any(character in value for character in "\r\n\x00")
    ):
        raise EventContractError(f"invalid {key}")
    return value


def _required_id(payload: dict, key: str) -> str:
    return validate_wire_id(payload.get(key), key)


def _bounded_string(
    value: object,
    key: str,
    limit: int,
    *,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str) or len(value) > limit or any(c in value for c in "\r\n\x00"):
        raise EventContractError(f"invalid {key}")
    if not allow_empty and not value:
        raise EventContractError(f"invalid {key}")
    return value


def _fingerprint(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clip_reply(text: str) -> str:
    """Match the existing voice-server boundary before building device JSON."""
    text = text.strip()
    if len(text) <= MAX_REPLY_CHARS:
        return text
    for index in range(MAX_REPLY_CHARS - 1, -1, -1):
        if text[index] in "。！？!?":
            return text[:index + 1]
    return text[:MAX_REPLY_CHARS]


def _validate_audio_url(value: object) -> str:
    url = _bounded_string(
        value,
        "audio_url",
        MAX_AUDIO_URL_CHARS,
        allow_empty=False,
    )
    try:
        parsed = urlparse(url)
        # Accessing port also rejects malformed values such as ``:not-a-port``.
        _ = parsed.port
    except ValueError as exc:
        raise EventContractError("invalid audio_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EventContractError("invalid audio_url")
    if parsed.username is not None or parsed.password is not None:
        raise EventContractError("invalid audio_url")
    return url


def materialize_device_action(descriptor: dict) -> dict:
    """Validate an internal action descriptor and add its opaque action ID."""
    if not isinstance(descriptor, dict):
        raise EventContractError("invalid device_action")
    action_type = descriptor.get("type")
    if action_type not in ACTION_TYPES:
        raise EventContractError("invalid device_action.type")

    action_id = str(uuid.uuid4())
    if action_type == "audio_playback":
        if set(descriptor) != {"type", "audio_url"}:
            raise EventContractError("invalid audio_playback fields")
        return {
            "action_id": action_id,
            "type": action_type,
            "audio_url": _validate_audio_url(descriptor.get("audio_url")),
        }

    expected = {
        "type",
        "camera_mode",
        "camera_requester",
        "transcript",
        "speaker",
        "speaker_label",
        "announce_endpoint",
    }
    if set(descriptor) != expected:
        raise EventContractError("invalid camera_capture fields")
    camera_mode = descriptor.get("camera_mode")
    camera_requester = descriptor.get("camera_requester")
    if camera_mode not in CAMERA_MODES:
        raise EventContractError("invalid camera_mode")
    if camera_requester not in CAMERA_REQUESTERS:
        raise EventContractError("invalid camera_requester")

    announce_endpoint = _bounded_string(
        descriptor.get("announce_endpoint"),
        "announce_endpoint",
        MAX_ANNOUNCE_ENDPOINT_CHARS,
        allow_empty=True,
    )
    if announce_endpoint and _ANNOUNCE_ENDPOINT_RE.fullmatch(announce_endpoint) is None:
        raise EventContractError("invalid announce_endpoint")

    return {
        "action_id": action_id,
        "type": action_type,
        "camera_mode": camera_mode,
        "camera_requester": camera_requester,
        "transcript": _bounded_string(
            descriptor.get("transcript"),
            "transcript",
            MAX_TEXT_CHARS,
            allow_empty=True,
        ),
        "speaker": _bounded_string(
            descriptor.get("speaker"),
            "speaker",
            MAX_SPEAKER_CHARS,
            allow_empty=False,
        ),
        "speaker_label": _bounded_string(
            descriptor.get("speaker_label"),
            "speaker_label",
            MAX_SPEAKER_LABEL_CHARS,
            allow_empty=True,
        ),
        "announce_endpoint": announce_endpoint,
    }


def validate_touch_opt_in(value: object) -> tuple[str, str]:
    """Validate the optional action-capable touch request envelope."""
    if not isinstance(value, dict) or set(value) != {
        "action_schema_version",
        "session_id",
        "event_id",
    }:
        raise EventContractError("invalid stackchan_device")
    if value.get("action_schema_version") != ACTION_SCHEMA_VERSION:
        raise EventContractError("unsupported action_schema_version")
    return (
        _required_id(value, "session_id"),
        _required_id(value, "event_id"),
    )


class ConversationEventStore:
    """Small session store intended for one local Stack-chan host."""

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        max_touch_requests: int = MAX_TOUCH_REQUESTS,
    ) -> None:
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        if type(max_touch_requests) is not int or max_touch_requests < 1:
            raise ValueError("max_touch_requests must be a positive integer")
        self._max_sessions = max_sessions
        self._max_touch_requests = max_touch_requests
        self._sessions: dict[str, _Session] = {}
        self._touch_requests: OrderedDict[tuple[str, str], _TouchRequest] = OrderedDict()
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._touch_requests.clear()

    def _evict_if_needed(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            oldest_id = min(self._sessions, key=lambda key: self._sessions[key].last_seen)
            del self._sessions[oldest_id]

    def _trim_touch_requests(self) -> None:
        while len(self._touch_requests) > self._max_touch_requests:
            completed_key = next(
                (key for key, item in self._touch_requests.items() if not item.processing),
                None,
            )
            if completed_key is None:
                return
            del self._touch_requests[completed_key]

    def _make_room_for_touch_request(self) -> None:
        while len(self._touch_requests) >= self._max_touch_requests:
            completed_key = next(
                (key for key, item in self._touch_requests.items() if not item.processing),
                None,
            )
            if completed_key is None:
                raise EventConflictError("speech_processing")
            del self._touch_requests[completed_key]

    @staticmethod
    def _response(
        schema_version: int,
        session_id: str,
        event_id: str,
        *,
        state: str,
        action: str,
        reply: dict | None = None,
        device_action: dict | None = None,
        reason: str = "",
    ) -> dict:
        response = {
            "ok": True,
            "schema_version": schema_version,
            "session_id": session_id,
            "event_id": event_id,
            "state": state,
            "action": action,
            "reply": reply,
            "reason": reason,
        }
        if schema_version == ACTION_SCHEMA_VERSION:
            response["device_action"] = device_action
        return response

    @staticmethod
    def _remember(session: _Session, event_id: str, fingerprint: str, response: dict) -> None:
        session.events[event_id] = _CachedEvent(fingerprint, copy.deepcopy(response))
        session.events.move_to_end(event_id)
        while len(session.events) > MAX_EVENTS_PER_SESSION:
            session.events.popitem(last=False)

    @staticmethod
    def _event_shape(payload: dict) -> tuple[int, str, str, str]:
        if not isinstance(payload, dict):
            raise EventContractError("JSON object required")
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise EventContractError("unsupported schema_version")

        session_id = _required_id(payload, "session_id")
        event_id = _required_id(payload, "event_id")
        event_type = payload.get("type")
        event_types = V2_EVENT_TYPES if schema_version == ACTION_SCHEMA_VERSION else V1_EVENT_TYPES
        if event_type not in event_types:
            raise EventContractError("invalid type")

        if event_type == "speech":
            allowed_keys = {"schema_version", "session_id", "event_id", "type", "text"}
        elif event_type == "playback_completed":
            allowed_keys = {"schema_version", "session_id", "event_id", "type", "reply_id"}
            if schema_version == ACTION_SCHEMA_VERSION:
                allowed_keys.add("status")
        elif event_type == "action_completed":
            allowed_keys = {
                "schema_version",
                "session_id",
                "event_id",
                "type",
                "action_id",
                "status",
            }
        else:
            allowed_keys = {"schema_version", "session_id", "event_id", "type"}
        if set(payload) != allowed_keys:
            raise EventContractError("unexpected or missing event fields")

        if event_type == "speech":
            text = payload.get("text")
            if (
                not isinstance(text, str)
                or not text.strip()
                or len(text) > MAX_TEXT_CHARS
                or "\x00" in text
            ):
                raise EventContractError("invalid text")
        if event_type == "playback_completed":
            _required_id(payload, "reply_id")
        if event_type == "action_completed":
            _required_id(payload, "action_id")
        if schema_version == ACTION_SCHEMA_VERSION and event_type in {
            "playback_completed",
            "action_completed",
        }:
            if payload.get("status") not in COMPLETION_STATUSES:
                raise EventContractError("invalid status")
        return schema_version, session_id, event_id, event_type

    def _find_cached_or_processing(
        self,
        session: _Session | None,
        event_id: str,
        fingerprint: str,
    ) -> dict | None:
        if session is None:
            return None
        cached = session.events.get(event_id)
        if cached is not None:
            if cached.fingerprint != fingerprint:
                raise EventConflictError("event_id_reused")
            session.last_seen = time.monotonic()
            return copy.deepcopy(cached.response)
        processing_fingerprint = session.processing_events.get(event_id)
        if processing_fingerprint is not None:
            if processing_fingerprint != fingerprint:
                raise EventConflictError("event_id_reused")
            raise EventConflictError("event_processing")
        return None

    def handle(self, payload: dict, reply_builder: Callable[[str], dict]) -> dict:
        schema_version, session_id, event_id, event_type = self._event_shape(payload)
        fingerprint = _fingerprint(payload)

        with self._lock:
            session = self._sessions.get(session_id)
            cached = self._find_cached_or_processing(session, event_id, fingerprint)
            if cached is not None:
                return cached

            if event_type == "start":
                if session is None:
                    self._evict_if_needed()
                    session = _Session()
                    self._sessions[session_id] = session
                else:
                    session.generation += 1
                    session.closed = False
                    session.accept_next = True
                    session.one_shot = False
                    session.pending_reply_id = None
                    session.pending_action_id = None
                    session.pending_action_type = None
                    session.events.clear()
                    session.processing_events.clear()
                response = self._response(
                    schema_version,
                    session_id,
                    event_id,
                    state="active",
                    action="listen",
                    reason="started",
                )
            else:
                if session is None:
                    raise EventConflictError("session_not_started")
                if session.closed:
                    raise EventConflictError("session_closed")

                if event_type == "face_touch":
                    session.accept_next = True
                    response = self._response(
                        schema_version,
                        session_id,
                        event_id,
                        state="active",
                        action="listen",
                        reason="face_touch",
                    )
                elif event_type == "stop":
                    session.generation += 1
                    session.closed = True
                    session.pending_reply_id = None
                    session.pending_action_id = None
                    session.pending_action_type = None
                    session.processing_events.clear()
                    response = self._response(
                        schema_version,
                        session_id,
                        event_id,
                        state="closed",
                        action="stopped",
                        reason="stopped",
                    )
                elif event_type == "playback_completed":
                    reply_id = _required_id(payload, "reply_id")
                    if session.pending_reply_id != reply_id:
                        raise EventConflictError("reply_id_mismatch")
                    session.pending_reply_id = None
                    failed = payload.get("status") == "failed"
                    response = self._response(
                        schema_version,
                        session_id,
                        event_id,
                        state="active",
                        action="listen",
                        reason="playback_failed" if failed else "playback_completed",
                    )
                elif event_type == "action_completed":
                    action_id = _required_id(payload, "action_id")
                    if session.pending_action_id != action_id:
                        raise EventConflictError("action_id_mismatch")
                    session.pending_action_id = None
                    session.pending_action_type = None
                    failed = payload.get("status") == "failed"
                    if session.one_shot:
                        session.closed = True
                        response = self._response(
                            schema_version,
                            session_id,
                            event_id,
                            state="closed",
                            action="stopped",
                            reason="action_failed" if failed else "action_completed",
                        )
                    else:
                        response = self._response(
                            schema_version,
                            session_id,
                            event_id,
                            state="active",
                            action="listen",
                            reason="action_failed" if failed else "action_completed",
                        )
                else:
                    if session.pending_reply_id is not None:
                        raise EventConflictError("reply_playback_pending")
                    if session.pending_action_id is not None:
                        raise EventConflictError("device_action_pending")
                    if session.processing_events:
                        raise EventConflictError("speech_processing")
                    session.processing_events[event_id] = fingerprint
                    session.last_seen = time.monotonic()
                    reserved_session = session
                    reserved_generation = session.generation
                    was_first_input = session.accept_next
                    response = None

            if response is not None:
                session.last_seen = time.monotonic()
                self._remember(session, event_id, fingerprint, response)
                return copy.deepcopy(response)

        # Slow model/search work is outside the store-wide lock.  The reserved
        # session identity and generation make stop/start able to supersede it.
        try:
            result = reply_builder(payload["text"].strip())
            if not isinstance(result, dict) or not result.get("success"):
                error = result.get("error") if isinstance(result, dict) else None
                raise RuntimeError(str(error or "chat backend failed"))

            action_descriptor = result.get("device_action")
            device_action = (
                materialize_device_action(action_descriptor)
                if schema_version == ACTION_SCHEMA_VERSION and isinstance(action_descriptor, dict)
                else None
            )
            raw_reply_text = result.get("response")
            if device_action is None and (
                not isinstance(raw_reply_text, str) or not raw_reply_text.strip()
            ):
                raise RuntimeError("chat backend returned an empty reply")
        except Exception:
            with self._lock:
                current = self._sessions.get(session_id)
                if current is reserved_session and current.generation == reserved_generation:
                    current.processing_events.pop(event_id, None)
            raise

        with self._lock:
            current = self._sessions.get(session_id)
            if current is not reserved_session or current.generation != reserved_generation:
                if current is not None and current.closed:
                    raise EventConflictError("session_closed")
                raise EventConflictError("session_not_started")

            current.processing_events.pop(event_id, None)
            current.accept_next = False
            reason = "first_input" if was_first_input else "active_conversation"
            if device_action is not None:
                current.pending_action_id = device_action["action_id"]
                current.pending_action_type = device_action["type"]
                response = self._response(
                    schema_version,
                    session_id,
                    event_id,
                    state="active",
                    action="perform",
                    device_action=device_action,
                    reason=reason,
                )
            else:
                reply_text = _clip_reply(raw_reply_text)
                reply_id = str(uuid.uuid4())
                current.pending_reply_id = reply_id
                response = self._response(
                    schema_version,
                    session_id,
                    event_id,
                    state="active",
                    action="reply",
                    reply={"id": reply_id, "text": reply_text},
                    reason=reason,
                )
                if schema_version == SCHEMA_VERSION:
                    stackchan_action = result.get("stackchan_action")
                    if isinstance(stackchan_action, dict):
                        response["stackchan_action"] = copy.deepcopy(stackchan_action)

            current.last_seen = time.monotonic()
            self._remember(current, event_id, fingerprint, response)
            return copy.deepcopy(response)

    def reserve_touch_request(
        self,
        stackchan_device: object,
        request_payload: dict,
    ) -> tuple[TouchReservation | None, dict | None]:
        """Reserve a v2 touch request or return its completed cached response."""
        session_id, event_id = validate_touch_opt_in(stackchan_device)
        fingerprint = _fingerprint(request_payload)
        key = (session_id, event_id)
        with self._lock:
            existing = self._touch_requests.get(key)
            if existing is not None:
                self._touch_requests.move_to_end(key)
                if existing.fingerprint != fingerprint:
                    raise EventConflictError("event_id_reused")
                if existing.processing:
                    raise EventConflictError("event_processing")
                return None, copy.deepcopy(existing.response)

            self._make_room_for_touch_request()
            self._touch_requests[key] = _TouchRequest(fingerprint=fingerprint)
            self._touch_requests.move_to_end(key)
            self._trim_touch_requests()
            return TouchReservation(session_id, event_id, fingerprint), None

    def fail_touch_request(self, reservation: TouchReservation) -> None:
        """Release a failed touch reservation so a later retry may run again."""
        key = (reservation.session_id, reservation.event_id)
        with self._lock:
            current = self._touch_requests.get(key)
            if current is not None and current.processing and current.fingerprint == reservation.fingerprint:
                del self._touch_requests[key]

    def complete_touch_request(
        self,
        reservation: TouchReservation,
        response: dict,
        *,
        device_action: dict | None = None,
    ) -> dict:
        """Cache a touch response and register its optional one-shot action."""
        key = (reservation.session_id, reservation.event_id)
        with self._lock:
            current = self._touch_requests.get(key)
            if (
                current is None
                or not current.processing
                or current.fingerprint != reservation.fingerprint
            ):
                raise EventConflictError("event_id_reused")

            if device_action is not None:
                existing_session = self._sessions.get(reservation.session_id)
                if existing_session is not None and not existing_session.closed:
                    raise EventConflictError("device_action_pending")
                self._evict_if_needed()
                self._sessions[reservation.session_id] = _Session(
                    accept_next=False,
                    one_shot=True,
                    pending_action_id=device_action["action_id"],
                    pending_action_type=device_action["type"],
                )

            current.processing = False
            current.response = copy.deepcopy(response)
            self._touch_requests.move_to_end(key)
            self._trim_touch_requests()
            return copy.deepcopy(response)

    def validate_pending_action(
        self,
        session_id: object,
        action_id: object,
        *,
        action_type: str | None = None,
    ) -> None:
        """Confirm correlation without completing or otherwise mutating it."""
        valid_session_id = validate_wire_id(session_id, "session_id")
        valid_action_id = validate_wire_id(action_id, "action_id")
        with self._lock:
            session = self._sessions.get(valid_session_id)
            if session is None:
                raise EventConflictError("session_not_started")
            if session.closed:
                raise EventConflictError("session_closed")
            if session.pending_action_id != valid_action_id:
                raise EventConflictError("action_id_mismatch")
            if action_type is not None and session.pending_action_type != action_type:
                raise EventConflictError("action_id_mismatch")


conversation_event_store = ConversationEventStore()

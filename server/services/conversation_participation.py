"""Offline participation reducer shared by the public and household servers.

The future scene reader supplies semantic observations, not keyword matches.
This module does not infer speakers, call an LLM, retain transcripts, write
memory, or control a device. It is intentionally not wired into live routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    ACTIVE = "active"
    QUIET = "quiet"
    MIC_OFF = "mic_off"


class EventKind(str, Enum):
    SPEECH = "speech"
    FACE_TOUCH = "face_touch"
    MIC_ON = "mic_on"
    MIC_OFF = "mic_off"
    TURN_BOUNDARY = "turn_boundary"


class Source(str, Enum):
    HUMAN = "human"
    SELF = "self"
    UNKNOWN = "unknown"


class Addressee(str, Enum):
    ASSISTANT = "assistant"
    HUMAN = "human"
    UNKNOWN = "unknown"


class Action(str, Enum):
    WAIT = "wait"
    LISTEN = "listen"
    REPLY = "reply"
    ANNOUNCE_QUIET = "announce_quiet"


@dataclass(frozen=True)
class Scene:
    """A provisional interpretation of the current conversational scene.

    ``called_assistant`` means a direct call using the robot's registered name,
    not merely mentioning that name. ``human_exchange`` means an established
    human-to-human exchange, not loudness or a count of nearby voices.
    ``reply_to_assistant`` covers an answer to the robot's previous question.
    Speaker A/B and addressee inference belong to the scene reader; uncertain
    identity must not be silently mapped to the household owner.
    """

    source: Source = Source.UNKNOWN
    addressee: Addressee = Addressee.UNKNOWN
    called_assistant: bool = False
    reply_to_assistant: bool = False
    human_exchange: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, Source) or not isinstance(self.addressee, Addressee):
            raise ValueError("scene source and addressee must be enums")
        flags = (self.called_assistant, self.reply_to_assistant, self.human_exchange)
        if any(type(value) is not bool for value in flags):
            raise ValueError("scene flags must be booleans")
        directed = self.called_assistant or self.reply_to_assistant
        if directed and self.addressee is not Addressee.ASSISTANT:
            raise ValueError("direct call/reply requires assistant addressee")
        if self.human_exchange and (directed or self.addressee is Addressee.ASSISTANT):
            raise ValueError("one observation cannot be both human exchange and assistant-directed")


@dataclass(frozen=True)
class Event:
    """Input event; playback_active refers to conversational TTS, not alarms.

    A turn boundary must be supplied by the input/turn-taking layer after a
    genuine gap, not fabricated from a punctuation mark by this reducer.
    """

    kind: EventKind
    scene: Scene | None = None
    playback_active: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind) or type(self.playback_active) is not bool:
            raise ValueError("invalid event kind or playback flag")
        if self.kind is EventKind.SPEECH:
            if not isinstance(self.scene, Scene):
                raise ValueError("speech requires a scene")
        elif self.scene is not None:
            raise ValueError("control events must not carry a speech scene")


@dataclass(frozen=True)
class State:
    mode: Mode = Mode.ACTIVE
    quiet_announcement_pending: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode) or type(self.quiet_announcement_pending) is not bool:
            raise ValueError("invalid participation state")
        if self.quiet_announcement_pending and self.mode is not Mode.QUIET:
            raise ValueError("quiet announcement requires quiet mode")


@dataclass(frozen=True)
class Decision:
    state: State
    action: Action
    reason: str
    stop_playback: bool = False

    @property
    def allow_conversation_history(self) -> bool:
        """Only an admitted conversational reply may enter normal history."""
        return self.action is Action.REPLY


def advance(state: State, event: Event) -> Decision:
    """Return a decision with no I/O; callers own serialized per-session state.

    Quiet announcements wait for an explicit turn-boundary event, and are
    emitted once per quiet transition. A new direct call or face touch cancels
    a pending announcement. Full microphone OFF requires explicit MIC_ON;
    face touch does not secretly override that setting.
    """
    if not isinstance(state, State) or not isinstance(event, Event):
        raise ValueError("state and event must use the participation contract")

    if event.kind is EventKind.MIC_OFF:
        return Decision(State(Mode.MIC_OFF), Action.WAIT, "mic_off", event.playback_active)
    if event.kind is EventKind.MIC_ON:
        # Duplicate enable commands must not wake an already-quiet session.
        if state.mode is Mode.MIC_OFF:
            return Decision(State(), Action.LISTEN, "mic_enabled")
        return Decision(state, Action.WAIT, "mic_already_enabled")
    if state.mode is Mode.MIC_OFF:
        return Decision(state, Action.WAIT, "mic_off")

    if event.kind is EventKind.FACE_TOUCH:
        return Decision(State(), Action.LISTEN, "face_touch", event.playback_active)

    if event.kind is EventKind.TURN_BOUNDARY:
        if state.quiet_announcement_pending and not event.playback_active:
            return Decision(State(Mode.QUIET), Action.ANNOUNCE_QUIET, "human_exchange")
        return Decision(state, Action.WAIT, "no_announcement_due")

    scene = event.scene
    assert scene is not None  # Event validates the speech payload.
    if scene.source is Source.SELF:
        return Decision(state, Action.WAIT, "self_audio")
    if scene.source is Source.UNKNOWN:
        return Decision(state, Action.WAIT, "uncertain_source")

    if state.mode is Mode.QUIET:
        if scene.called_assistant:
            return Decision(State(), Action.REPLY, "direct_call", event.playback_active)
        return Decision(state, Action.WAIT, "quiet_ambient")

    if scene.addressee is Addressee.ASSISTANT:
        reason = "direct_call" if scene.called_assistant else "assistant_directed"
        return Decision(state, Action.REPLY, reason, event.playback_active)
    if scene.human_exchange:
        return Decision(State(Mode.QUIET, True), Action.WAIT, "human_exchange", event.playback_active)
    return Decision(state, Action.WAIT, "not_addressed_to_assistant")

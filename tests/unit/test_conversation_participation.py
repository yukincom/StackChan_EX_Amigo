"""Synthetic scene replays; no household transcripts or acoustic accuracy claims."""

import pytest

from services.conversation_participation import (
    Action, Addressee, Event, EventKind, Mode, Scene, Source, State, advance,
)


def speech(*, target=Addressee.UNKNOWN, source=Source.HUMAN, called=False,
           reply=False, exchange=False, playing=False):
    return Event(EventKind.SPEECH, Scene(source, target, called, reply, exchange), playing)


def replay(events, initial=None):
    state = initial or State()
    decisions = []
    for event in events:
        decision = advance(state, event)
        decisions.append(decision)
        state = decision.state
    return decisions


def test_active_conversation_does_not_need_name_on_every_turn():
    decisions = replay([
        speech(target=Addressee.ASSISTANT),
        speech(target=Addressee.ASSISTANT, reply=True),
    ])
    assert [d.action for d in decisions] == [Action.REPLY, Action.REPLY]
    assert all(d.allow_conversation_history for d in decisions)


def test_human_request_then_human_answer_yields_once_at_a_gap():
    # Fictional board-game scene: A asks B for a piece; B supplies it.
    decisions = replay([
        speech(target=Addressee.HUMAN),
        speech(target=Addressee.HUMAN, exchange=True, playing=True),
        speech(target=Addressee.HUMAN, exchange=True),
        Event(EventKind.TURN_BOUNDARY),
        Event(EventKind.TURN_BOUNDARY),
    ])
    assert [d.action for d in decisions] == [Action.WAIT] * 3 + [Action.ANNOUNCE_QUIET, Action.WAIT]
    assert decisions[0].state.mode is Mode.ACTIVE
    assert decisions[1].state.mode is Mode.QUIET
    assert decisions[1].stop_playback
    assert not any(d.allow_conversation_history for d in decisions)


def test_announcement_waits_until_playback_is_stopped():
    decisions = replay([
        speech(target=Addressee.HUMAN, exchange=True),
        Event(EventKind.TURN_BOUNDARY, playback_active=True),
        Event(EventKind.TURN_BOUNDARY),
    ])
    assert decisions[1].state.quiet_announcement_pending
    assert decisions[1].action is Action.WAIT
    assert decisions[2].action is Action.ANNOUNCE_QUIET


@pytest.mark.parametrize("source", [Source.SELF, Source.UNKNOWN])
def test_self_audio_or_unknown_source_cannot_wake_or_interrupt(source):
    decision = advance(State(Mode.QUIET), speech(
        target=Addressee.ASSISTANT, called=True, source=source, playing=True,
    ))
    assert decision.state.mode is Mode.QUIET
    assert decision.action is Action.WAIT
    assert not decision.stop_playback
    assert not decision.allow_conversation_history


def test_mention_is_not_a_call_and_direct_call_resumes():
    decisions = replay([
        speech(target=Addressee.HUMAN),  # talking ABOUT the robot
        speech(target=Addressee.ASSISTANT, called=True),
        speech(target=Addressee.ASSISTANT, reply=True),
    ], State(Mode.QUIET))
    assert [d.action for d in decisions] == [Action.WAIT, Action.REPLY, Action.REPLY]


@pytest.mark.parametrize("mode", [Mode.ACTIVE, Mode.QUIET])
def test_face_touch_starts_listening_and_does_not_create_a_fake_turn(mode):
    decision = advance(State(mode), Event(EventKind.FACE_TOUCH, playback_active=True))
    assert decision.state == State()
    assert decision.action is Action.LISTEN
    assert decision.stop_playback
    assert not decision.allow_conversation_history


@pytest.mark.parametrize("resume", [
    Event(EventKind.FACE_TOUCH),
    speech(target=Addressee.ASSISTANT, called=True),
])
def test_resuming_cancels_pending_quiet_announcement(resume):
    decisions = replay([
        speech(target=Addressee.HUMAN, exchange=True), resume,
        Event(EventKind.TURN_BOUNDARY),
    ])
    assert decisions[-1].action is Action.WAIT
    assert decisions[-1].state.mode is Mode.ACTIVE


def test_second_human_exchange_can_yield_again_after_a_real_resumption():
    decisions = replay([
        speech(exchange=True), Event(EventKind.TURN_BOUNDARY),
        speech(target=Addressee.ASSISTANT, called=True),
        speech(exchange=True), Event(EventKind.TURN_BOUNDARY),
        Event(EventKind.TURN_BOUNDARY),
    ])
    assert sum(d.action is Action.ANNOUNCE_QUIET for d in decisions) == 2
    assert decisions[-1].state.mode is Mode.QUIET


def test_full_mic_off_cannot_be_overridden_by_name_or_touch():
    decisions = replay([
        Event(EventKind.MIC_OFF, playback_active=True),
        Event(EventKind.FACE_TOUCH),
        speech(target=Addressee.ASSISTANT, called=True),
        Event(EventKind.MIC_ON),
        Event(EventKind.FACE_TOUCH),
    ])
    assert decisions[0].stop_playback
    assert all(d.state.mode is Mode.MIC_OFF for d in decisions[:3])
    assert [d.action for d in decisions] == [Action.WAIT] * 3 + [Action.LISTEN] * 2
    assert not any(d.allow_conversation_history for d in decisions)


def test_repeated_mic_on_does_not_cancel_quiet_mode():
    state = State(Mode.QUIET, True)
    decision = advance(state, Event(EventKind.MIC_ON))
    assert decision.state == state
    assert decision.action is Action.WAIT


def test_quiet_does_not_resume_on_unconfirmed_call():
    decision = advance(State(Mode.QUIET), speech(target=Addressee.ASSISTANT))
    assert decision.action is Action.WAIT
    assert decision.state.mode is Mode.QUIET


def test_sessions_are_independent():
    first = advance(State(), speech(exchange=True)).state
    second = advance(State(), speech(target=Addressee.ASSISTANT)).state
    assert first.mode is Mode.QUIET
    assert second.mode is Mode.ACTIVE


@pytest.mark.parametrize("factory", [
    lambda: Scene(called_assistant=True),
    lambda: Scene(reply_to_assistant=True),
    lambda: Scene(Source.HUMAN, Addressee.ASSISTANT, human_exchange=True),
    lambda: Scene(source="human"),
    lambda: Scene(called_assistant="false"),
    lambda: Event(EventKind.SPEECH),
    lambda: Event(EventKind.FACE_TOUCH, Scene()),
    lambda: Event("face_touch"),
    lambda: State(Mode.ACTIVE, True),
    lambda: State("quiet"),
])
def test_inconsistent_or_untyped_interpretations_are_rejected(factory):
    with pytest.raises(ValueError):
        factory()

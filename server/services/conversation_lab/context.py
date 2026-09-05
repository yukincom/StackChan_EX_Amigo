"""Ephemeral utterance context and explicit character identity."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from ..conversation_participation import State

DEFAULT_HISTORY_UTTERANCES = 16
DEFAULT_CONTEXT_CHARS = 12000

@dataclass(frozen=True)
class Utterance:
    id: str
    role: str
    text: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("utterance id must be nonempty")
        if self.role not in {"human", "assistant", "unknown"}:
            raise ValueError("unknown utterance role")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("utterance text must be nonempty")


class TextContext:
    """Ephemeral scene context. Quiet speech goes here, never into normal memory.

    history_utterances counts prior human AND assistant utterances, not pairs.
    The current utterance is additional. Whole old utterances are evicted;
    overlong current input is rejected rather than silently truncated.
    """

    def __init__(self, history_utterances=DEFAULT_HISTORY_UTTERANCES,
                 max_chars=DEFAULT_CONTEXT_CHARS):
        if type(history_utterances) is not int or history_utterances < 0:
            raise ValueError("history_utterances must be a nonnegative integer")
        if type(max_chars) is not int or max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.history_utterances = history_utterances
        self.max_chars = max_chars
        self._items: list[Utterance] = []

    def clear(self):
        self._items.clear()

    def append(self, item: Utterance):
        if len(item.text) > self.max_chars:
            raise ValueError("current utterance exceeds context character budget")
        self._items.append(item)
        while (len(self._items) > self.history_utterances
               or sum(len(i.text) for i in self._items) > self.max_chars):
            self._items.pop(0)

    def payload(self, current: Utterance, state: State, aliases: list[str]):
        if len(current.text) > self.max_chars:
            raise ValueError("current utterance exceeds context character budget")
        history = list(self._items)
        while history and sum(len(i.text) for i in history) + len(current.text) > self.max_chars:
            history.pop(0)
        return {"state": state.mode.value, "assistant_names": aliases,
                "history": [asdict(i) for i in history], "current": asdict(current)}

def validate_names(aliases):
    if (not isinstance(aliases, list) or not aliases
            or any(not isinstance(a, str) or not a.strip() or a != a.strip() for a in aliases)
            or len(set(aliases)) != len(aliases)):
        raise ValueError("assistant_names must be explicit, nonempty and unique")

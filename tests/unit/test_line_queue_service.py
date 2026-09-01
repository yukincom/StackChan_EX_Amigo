from __future__ import annotations

import base64
import json
import queue

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from operation_journal import OperationJournal
from services import line_queue_service


EVENT_ID = "01H810YECXQQZ37VAXPF6H9E6T"
USER_ID = "U0123456789abcdef0123456789abcdef"
KEY = bytes(range(32))
ENCODED_KEY = base64.b64encode(KEY).decode("ascii")
PULL_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{'a' * 32}/queues/{'b' * 32}/messages/pull"
)


def _encrypted_body(*, message="秘密の家庭メッセージ", key=KEY):
    nonce = bytes(range(12))
    plaintext = json.dumps(
        {"user_id": USER_ID, "message": message, "timestamp_ms": 1_700_000_000_000},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(
        nonce,
        plaintext,
        f"stackchan-line-relay:v1:{EVENT_ID}".encode("ascii"),
    )
    return json.dumps(
        {
            "v": 1,
            "event_id": EVENT_ID,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        },
        separators=(",", ":"),
    )


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _PollClient:
    def __init__(self, deliveries):
        self.key = KEY
        self.deliveries = deliveries
        self.settled = None

    def pull(self):
        return self.deliveries

    def settle(self, *, acknowledgements, retries):
        self.settled = (acknowledgements, retries)


def _isolate_journal(monkeypatch, tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    monkeypatch.setattr(line_queue_service, "get_operation_journal", lambda: journal)
    monkeypatch.setattr(line_queue_service, "_LINE_NOTIFICATION_QUEUE", queue.Queue(maxsize=10))
    monkeypatch.setattr(line_queue_service, "_LINE_NOTIFICATION_QUEUED", set())
    monkeypatch.setattr(line_queue_service, "ensure_line_notification_worker", lambda: None)
    return journal


def test_encrypted_line_event_round_trip_and_tamper_rejection():
    event = line_queue_service.decode_line_event(_encrypted_body(), KEY)
    assert event == line_queue_service.LineQueueEvent(
        EVENT_ID,
        USER_ID,
        "秘密の家庭メッセージ",
        1_700_000_000_000,
    )

    envelope = json.loads(_encrypted_body())
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    with pytest.raises(line_queue_service.LineQueueDecryptError):
        line_queue_service.decode_line_event(json.dumps(envelope), KEY)


def test_client_uses_fixed_cloudflare_endpoint_and_bearer_only_in_header():
    body = _encrypted_body()
    session = _Session([
        _Response({
            "success": True,
            "result": {
                "messages": [{"id": "msg_1", "lease_id": "lease-1", "body": body}],
            },
        }),
        _Response({"success": True, "result": {}}),
    ])
    client = line_queue_service.CloudflareLineQueueClient(
        PULL_URL,
        "token-that-is-long-enough",
        ENCODED_KEY,
        session=session,
    )

    deliveries = client.pull()
    client.settle(acknowledgements=[deliveries[0].lease_id], retries=[])

    assert len(deliveries) == 1
    assert session.calls[0][0] == PULL_URL
    assert session.calls[1][0].endswith("/messages/ack")
    assert "token-that-is-long-enough" not in session.calls[0][0]
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer token-that-is-long-enough"
    assert session.calls[1][1]["json"] == {
        "acks": [{"lease_id": "lease-1"}],
        "retries": [],
    }


def test_client_rejects_non_cloudflare_pull_url():
    with pytest.raises(line_queue_service.LineQueueConfigError, match="allowed"):
        line_queue_service.CloudflareLineQueueClient(
            "https://attacker.example/messages/pull",
            "token-that-is-long-enough",
            ENCODED_KEY,
        )


def test_poll_acknowledges_only_after_private_local_persistence(monkeypatch, tmp_path):
    journal = _isolate_journal(monkeypatch, tmp_path)
    client = _PollClient([
        line_queue_service.QueueDelivery("msg_1", "lease-1", _encrypted_body()),
    ])
    monkeypatch.setattr(line_queue_service, "line_queue_configured", lambda: True)
    monkeypatch.setattr(line_queue_service, "get_line_queue_client", lambda: client)
    monkeypatch.setattr(line_queue_service, "find_by_user_id", lambda _user_id: {"name": "fixture"})

    line_queue_service.poll_line_queue()

    assert client.settled == (["lease-1"], [])
    operation = next(iter(journal.latest().values()))
    assert operation["kind"] == "line_notification"
    assert operation["state"] == "accepted"
    assert "秘密の家庭メッセージ" not in journal.journal_path.read_text(encoding="utf-8")
    assert "秘密の家庭メッセージ" in journal.load_payload(operation["op_id"], "request.json").decode()


def test_poll_retries_when_local_member_mapping_is_missing(monkeypatch, tmp_path):
    journal = _isolate_journal(monkeypatch, tmp_path)
    client = _PollClient([
        line_queue_service.QueueDelivery("msg_1", "lease-1", _encrypted_body()),
    ])
    monkeypatch.setattr(line_queue_service, "line_queue_configured", lambda: True)
    monkeypatch.setattr(line_queue_service, "get_line_queue_client", lambda: client)
    monkeypatch.setattr(line_queue_service, "find_by_user_id", lambda _user_id: None)

    line_queue_service.poll_line_queue()

    assert client.settled == ([], [("lease-1", 60)])
    assert journal.latest() == {}


def test_poll_discards_poison_envelope_but_retries_wrong_key(monkeypatch, tmp_path):
    _isolate_journal(monkeypatch, tmp_path)
    client = _PollClient([
        line_queue_service.QueueDelivery("msg_1", "lease-poison", "{bad-json"),
        line_queue_service.QueueDelivery(
            "msg_2",
            "lease-key",
            _encrypted_body(key=bytes(reversed(range(32)))),
        ),
    ])
    monkeypatch.setattr(line_queue_service, "line_queue_configured", lambda: True)
    monkeypatch.setattr(line_queue_service, "get_line_queue_client", lambda: client)

    line_queue_service.poll_line_queue()

    assert client.settled == (["lease-poison"], [("lease-key", 60)])


def test_persisted_notification_processes_once_with_stable_voice_key(monkeypatch, tmp_path):
    journal = _isolate_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(line_queue_service, "find_by_user_id", lambda _user_id: {"name": "fixture"})
    event = line_queue_service.decode_line_event(_encrypted_body(), KEY)
    assert line_queue_service._persist_line_event(event)
    operation = next(iter(journal.latest().values()))
    calls = []

    def process(user_id, message, *, delivery_key):
        calls.append((user_id, message, delivery_key))
        return {"success": True}

    monkeypatch.setattr(line_queue_service, "process_notification", process)
    assert line_queue_service._process_line_operation(operation["op_id"])

    assert calls == [(USER_ID, "秘密の家庭メッセージ", operation["op_id"])]
    assert journal.latest()[operation["op_id"]]["state"] == "succeeded"
    assert not (journal.spool_dir / operation["op_id"]).exists()

    # Cloud redelivery is acknowledged from the existing deterministic record.
    assert line_queue_service._persist_line_event(event)
    assert len(journal.latest()) == 1

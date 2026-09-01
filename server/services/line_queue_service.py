"""Authenticated Cloudflare Queue pull transport for LINE notifications.

The public webhook encrypts the private payload. This module pulls ciphertext,
persists a local operation before acknowledging the cloud queue, and processes
the notification on a background worker.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import queue
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import config
from member_loader import find_by_user_id
from observability import emit_trace
from operation_journal import JournalError, get_operation_journal
from services.notification_service import process_notification


_PULL_PATH_RE = re.compile(
    r"/client/v4/accounts/[0-9a-f]{32}/queues/[0-9a-f]{32}/messages/pull"
)
_LINE_EVENT_ID_RE = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
_LINE_USER_ID_RE = re.compile(r"U[0-9a-fA-F]{32}")
_QUEUE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MAX_LEASE_ID_CHARS = 4096
_MAX_ENVELOPE_CHARS = 24_000
_MAX_MESSAGE_CHARS = 5_000
_AAD_PREFIX = "stackchan-line-relay:v1:"

_LINE_NOTIFICATION_QUEUE: queue.Queue[str] = queue.Queue(maxsize=100)
_LINE_NOTIFICATION_LOCK = threading.Lock()
_LINE_NOTIFICATION_QUEUED: set[str] = set()
_LINE_NOTIFICATION_WORKER_STARTED = False
_LINE_NOTIFICATION_WORKER: threading.Thread | None = None
_DEFAULT_CLIENT: "CloudflareLineQueueClient | None" = None


class LineQueueError(RuntimeError):
    pass


class LineQueueConfigError(LineQueueError):
    pass


class LineQueueTransportError(LineQueueError):
    pass


class LineQueueEnvelopeError(LineQueueError):
    pass


class LineQueueDecryptError(LineQueueError):
    pass


@dataclass(frozen=True)
class QueueDelivery:
    queue_message_id: str
    lease_id: str
    body: str


@dataclass(frozen=True)
class LineQueueEvent:
    event_id: str
    user_id: str
    message: str
    timestamp_ms: int


def _decode_base64(value: object, *, expected_bytes: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or len(value) > _MAX_ENVELOPE_CHARS:
        raise LineQueueEnvelopeError("encoded value is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LineQueueEnvelopeError("encoded value is invalid") from exc
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise LineQueueEnvelopeError("encoded value has invalid length")
    return decoded


def decode_relay_key(encoded_key: str) -> bytes:
    try:
        return _decode_base64(encoded_key, expected_bytes=32)
    except LineQueueEnvelopeError as exc:
        raise LineQueueConfigError("LINE_RELAY_KEY must be base64 for 32 bytes") from exc


def decode_line_event(body: str, key: bytes) -> LineQueueEvent:
    if not isinstance(body, str) or not 1 <= len(body) <= _MAX_ENVELOPE_CHARS:
        raise LineQueueEnvelopeError("queue body is invalid")
    try:
        envelope = json.loads(body)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise LineQueueEnvelopeError("queue body is malformed") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "v", "event_id", "nonce", "ciphertext",
    }:
        raise LineQueueEnvelopeError("queue envelope shape is invalid")
    event_id = envelope["event_id"]
    if envelope["v"] != 1 or not isinstance(event_id, str) or not _LINE_EVENT_ID_RE.fullmatch(event_id):
        raise LineQueueEnvelopeError("queue envelope version or event ID is invalid")

    nonce = _decode_base64(envelope["nonce"], expected_bytes=12)
    ciphertext = _decode_base64(envelope["ciphertext"])
    if not 16 <= len(ciphertext) <= 16_384:
        raise LineQueueEnvelopeError("queue ciphertext length is invalid")
    aad = f"{_AAD_PREFIX}{event_id}".encode("ascii")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise LineQueueDecryptError("queue ciphertext authentication failed") from exc
    if len(plaintext) > 12_000:
        raise LineQueueEnvelopeError("queue plaintext is too large")
    try:
        payload = json.loads(plaintext)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LineQueueEnvelopeError("queue plaintext is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"user_id", "message", "timestamp_ms"}:
        raise LineQueueEnvelopeError("queue plaintext shape is invalid")

    user_id = payload["user_id"]
    message = payload["message"]
    timestamp_ms = payload["timestamp_ms"]
    if not isinstance(user_id, str) or not _LINE_USER_ID_RE.fullmatch(user_id):
        raise LineQueueEnvelopeError("LINE user ID is invalid")
    if not isinstance(message, str) or not 1 <= len(message) <= _MAX_MESSAGE_CHARS or "\x00" in message:
        raise LineQueueEnvelopeError("LINE message is invalid")
    if type(timestamp_ms) is not int or timestamp_ms <= 0:
        raise LineQueueEnvelopeError("LINE timestamp is invalid")
    return LineQueueEvent(event_id, user_id, message, timestamp_ms)


def _validate_pull_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.cloudflare.com"
        or not _PULL_PATH_RE.fullmatch(parsed.path)
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LineQueueConfigError("LINE_QUEUE_PULL_URL is not an allowed Cloudflare Queue URL")
    return value, f"https://api.cloudflare.com{parsed.path.removesuffix('/pull')}/ack"


class CloudflareLineQueueClient:
    def __init__(
        self,
        pull_url: str,
        api_token: str,
        relay_key: str,
        *,
        session=None,
    ):
        self.pull_url, self.ack_url = _validate_pull_url(str(pull_url).strip())
        self.api_token = str(api_token).strip()
        if not 16 <= len(self.api_token) <= 4096:
            raise LineQueueConfigError("LINE_QUEUE_API_TOKEN is invalid")
        self.key = decode_relay_key(str(relay_key).strip())
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_json(response) -> dict:
        try:
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LineQueueTransportError("Cloudflare Queue request failed") from exc
        if not isinstance(data, dict) or data.get("success") is not True:
            raise LineQueueTransportError("Cloudflare Queue returned an invalid response")
        return data

    def pull(self, *, batch_size: int = 10, visibility_timeout_ms: int = 300_000) -> list[QueueDelivery]:
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise LineQueueConfigError("queue batch size is invalid")
        if type(visibility_timeout_ms) is not int or not 1_000 <= visibility_timeout_ms <= 43_200_000:
            raise LineQueueConfigError("queue visibility timeout is invalid")
        try:
            response = self.session.post(
                self.pull_url,
                headers=self._headers(),
                json={
                    "batch_size": batch_size,
                    "visibility_timeout_ms": visibility_timeout_ms,
                },
                timeout=(5, 20),
            )
        except requests.RequestException as exc:
            raise LineQueueTransportError("Cloudflare Queue pull failed") from exc
        data = self._response_json(response)
        result = data.get("result")
        messages = result.get("messages") if isinstance(result, dict) else None
        if not isinstance(messages, list) or len(messages) > batch_size:
            raise LineQueueTransportError("Cloudflare Queue message batch is invalid")

        deliveries: list[QueueDelivery] = []
        for message in messages:
            if not isinstance(message, dict):
                raise LineQueueTransportError("Cloudflare Queue message is invalid")
            queue_message_id = message.get("id")
            lease_id = message.get("lease_id")
            body = message.get("body")
            if (
                not isinstance(queue_message_id, str)
                or not _QUEUE_ID_RE.fullmatch(queue_message_id)
                or not isinstance(lease_id, str)
                or not 1 <= len(lease_id) <= _MAX_LEASE_ID_CHARS
                or not isinstance(body, str)
            ):
                raise LineQueueTransportError("Cloudflare Queue message fields are invalid")
            deliveries.append(QueueDelivery(queue_message_id, lease_id, body))
        return deliveries

    def settle(
        self,
        *,
        acknowledgements: list[str],
        retries: list[tuple[str, int]],
    ) -> None:
        acks: list[dict[str, str]] = []
        retry_items: list[dict[str, str | int]] = []
        seen: set[str] = set()
        for lease_id in acknowledgements:
            if not isinstance(lease_id, str) or not 1 <= len(lease_id) <= _MAX_LEASE_ID_CHARS:
                raise LineQueueConfigError("queue lease ID is invalid")
            if lease_id not in seen:
                acks.append({"lease_id": lease_id})
                seen.add(lease_id)
        for lease_id, delay_seconds in retries:
            if (
                not isinstance(lease_id, str)
                or not 1 <= len(lease_id) <= _MAX_LEASE_ID_CHARS
                or type(delay_seconds) is not int
                or not 0 <= delay_seconds <= 43_200
            ):
                raise LineQueueConfigError("queue retry is invalid")
            if lease_id not in seen:
                retry_items.append({"lease_id": lease_id, "delay_seconds": delay_seconds})
                seen.add(lease_id)
        if not acks and not retry_items:
            return
        try:
            response = self.session.post(
                self.ack_url,
                headers=self._headers(),
                json={"acks": acks, "retries": retry_items},
                timeout=(5, 20),
            )
        except requests.RequestException as exc:
            raise LineQueueTransportError("Cloudflare Queue acknowledgement failed") from exc
        self._response_json(response)


def line_queue_configured() -> bool:
    values = (
        config.LINE_QUEUE_PULL_URL,
        config.LINE_QUEUE_API_TOKEN,
        config.LINE_RELAY_KEY,
    )
    if not all(values):
        return False
    try:
        _validate_pull_url(values[0])
        if not 16 <= len(values[1]) <= 4096:
            raise LineQueueConfigError("LINE_QUEUE_API_TOKEN is invalid")
        decode_relay_key(values[2])
    except LineQueueConfigError:
        return False
    return True


def get_line_queue_client() -> CloudflareLineQueueClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = CloudflareLineQueueClient(
            config.LINE_QUEUE_PULL_URL,
            config.LINE_QUEUE_API_TOKEN,
            config.LINE_RELAY_KEY,
        )
    return _DEFAULT_CLIENT


def _line_operation_id(event_id: str) -> str:
    if not _LINE_EVENT_ID_RE.fullmatch(event_id):
        raise LineQueueEnvelopeError("LINE event ID is invalid")
    return hashlib.sha256(f"line_notification\0{event_id}".encode("ascii")).hexdigest()[:32]


def _queue_line_operation(op_id: str) -> None:
    with _LINE_NOTIFICATION_LOCK:
        if op_id in _LINE_NOTIFICATION_QUEUED:
            return
        try:
            _LINE_NOTIFICATION_QUEUE.put_nowait(op_id)
        except queue.Full as exc:
            raise LineQueueError("local LINE notification queue is full") from exc
        _LINE_NOTIFICATION_QUEUED.add(op_id)


def _enqueue_persisted_line_notifications() -> None:
    journal = get_operation_journal()
    for record in journal.pending("line_notification"):
        try:
            _queue_line_operation(record["op_id"])
        except LineQueueError:
            return


def _persist_line_event(event: LineQueueEvent) -> bool:
    if find_by_user_id(event.user_id) is None:
        return False
    private_payload = json.dumps(
        {
            "user_id": event.user_id,
            "message": event.message,
            "timestamp_ms": event.timestamp_ms,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    journal = get_operation_journal()
    operation, _created = journal.create_once(
        "line_notification",
        _line_operation_id(event.event_id),
        owner="scheduler",
        payloads={"request.json": private_payload},
    )
    if operation["state"] in {"accepted", "retry_wait"}:
        _queue_line_operation(operation["op_id"])
    return True


def _process_line_operation(op_id: str) -> bool:
    journal = get_operation_journal()
    try:
        private_payload = json.loads(journal.load_payload(op_id, "request.json"))
        if not isinstance(private_payload, dict) or set(private_payload) != {
            "user_id", "message", "timestamp_ms",
        }:
            raise LineQueueEnvelopeError("local LINE payload shape is invalid")
        user_id = private_payload["user_id"]
        message = private_payload["message"]
        if (
            not isinstance(user_id, str)
            or not _LINE_USER_ID_RE.fullmatch(user_id)
            or find_by_user_id(user_id) is None
            or not isinstance(message, str)
            or not 1 <= len(message) <= _MAX_MESSAGE_CHARS
        ):
            raise LineQueueEnvelopeError("local LINE payload is invalid")
    except (JournalError, LineQueueEnvelopeError, json.JSONDecodeError, UnicodeDecodeError):
        journal.transition(op_id, "failed", owner="worker", stage="error")
        return False

    journal.transition(op_id, "running", owner="worker", stage="processing")
    try:
        result = process_notification(user_id, message, delivery_key=op_id)
        if not isinstance(result, dict) or result.get("success") is not True:
            journal.transition(op_id, "retry_wait", owner="worker", stage="error")
            return False
        journal.transition(op_id, "succeeded", owner="worker", stage="complete")
        journal.discard_payloads(op_id)
        return True
    except Exception as exc:
        try:
            latest = journal.latest().get(op_id)
            if latest and latest["state"] == "running":
                journal.transition(op_id, "retry_wait", owner="worker", stage="error")
        except JournalError:
            pass
        emit_trace("line", "process", "error", error_type=type(exc).__name__)
        return False


def _line_notification_worker_loop() -> None:
    while True:
        op_id = _LINE_NOTIFICATION_QUEUE.get()
        try:
            _process_line_operation(op_id)
        except Exception as exc:
            emit_trace("line", "process", "error", error_type=type(exc).__name__)
        finally:
            with _LINE_NOTIFICATION_LOCK:
                _LINE_NOTIFICATION_QUEUED.discard(op_id)
            _LINE_NOTIFICATION_QUEUE.task_done()


def ensure_line_notification_worker() -> None:
    global _LINE_NOTIFICATION_WORKER_STARTED, _LINE_NOTIFICATION_WORKER
    with _LINE_NOTIFICATION_LOCK:
        if (
            _LINE_NOTIFICATION_WORKER_STARTED
            and _LINE_NOTIFICATION_WORKER is not None
            and _LINE_NOTIFICATION_WORKER.is_alive()
        ):
            return
        worker = threading.Thread(
            target=_line_notification_worker_loop,
            name="line-notification-worker",
            daemon=True,
        )
        worker.start()
        _LINE_NOTIFICATION_WORKER = worker
        _LINE_NOTIFICATION_WORKER_STARTED = True
    _enqueue_persisted_line_notifications()


def line_notification_worker_ready() -> bool:
    return bool(
        _LINE_NOTIFICATION_WORKER_STARTED
        and _LINE_NOTIFICATION_WORKER is not None
        and _LINE_NOTIFICATION_WORKER.is_alive()
    )


def poll_line_queue() -> None:
    """Pull at the scheduler interval; acknowledge only after local persistence."""
    if not line_queue_configured():
        return
    ensure_line_notification_worker()
    try:
        _enqueue_persisted_line_notifications()
        client = get_line_queue_client()
        deliveries = client.pull()
        acknowledgements: list[str] = []
        retries: list[tuple[str, int]] = []
        for delivery in deliveries:
            try:
                event = decode_line_event(delivery.body, client.key)
                if _persist_line_event(event):
                    acknowledgements.append(delivery.lease_id)
                else:
                    retries.append((delivery.lease_id, config.POLL_INTERVAL))
            except LineQueueDecryptError:
                # A key mismatch can be repaired locally, so retain it for retry.
                retries.append((delivery.lease_id, config.POLL_INTERVAL))
            except LineQueueEnvelopeError:
                # Malformed authenticated queue data is poison; do not loop forever.
                acknowledgements.append(delivery.lease_id)
            except (JournalError, LineQueueError):
                # Local durability failures must leave the cloud copy available.
                retries.append((delivery.lease_id, config.POLL_INTERVAL))
        client.settle(acknowledgements=acknowledgements, retries=retries)
        emit_trace(
            "line",
            "poll",
            "ok",
            tags={"source": "scheduler"},
            metrics={"queue_size": len(deliveries)},
        )
    except Exception as exc:
        emit_trace("line", "poll", "error", error_type=type(exc).__name__)
        print("[LINE] Queue poll failed")

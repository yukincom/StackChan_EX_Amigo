#include <cassert>
#include <functional>
#include <string>
#include <vector>
#include "share/ConversationSession.h"

using Session = stackchan::ConversationSession;
using Outcome = Session::Outcome;
using Phase = Session::Phase;

static unsigned nextId = 0;
static std::string makeId() { return "v2-test-" + std::to_string(++nextId); }
static std::string japanese(size_t count) {
  std::string result;
  while (count--) result += "あ";
  return result;
}

static void camera(ArduinoJson::JsonVariant action) {
  action["action_id"] = "action-1";
  action["type"] = "camera_capture";
  action["camera_mode"] = "transient";
  action["camera_requester"] = "user";
  action["transcript"] = "見てください";
  action["speaker"] = "master";
  action["speaker_label"] = "";
  action["announce_endpoint"] = "camera_start-1";
}

struct Transport : stackchan::ConversationTransport {
  int status = 200;
  bool perform = false;
  bool audio = false;
  bool oneShot = false;
  std::string rawResponse;
  std::vector<std::string> requests;
  std::function<void(ArduinoJson::JsonDocument&)> mutate;
  std::function<void()> observer;

  int post(const std::string& body, std::string& response) override {
    requests.push_back(body);
    if (observer) observer();
    ArduinoJson::JsonDocument request, reply;
    assert(!deserializeJson(request, body));
    assert(request["schema_version"] == 2);
    const std::string type = request["type"].as<std::string>();
    reply["ok"] = true;
    reply["schema_version"] = 2;
    reply["session_id"] = request["session_id"];
    reply["event_id"] = request["event_id"];
    const bool closed = type == "stop" || (oneShot && type == "action_completed");
    reply["state"] = closed ? "closed" : "active";
    reply["action"] = closed ? "stopped" : "listen";
    reply["reply"] = nullptr;
    reply["device_action"] = nullptr;
    if (type == "speech") {
      assert(request.size() == 5);
      if (perform) {
        reply["action"] = "perform";
        if (audio) {
          reply["device_action"]["action_id"] = "action-1";
          reply["device_action"]["type"] = "audio_playback";
          reply["device_action"]["audio_url"] = "https://example.test/song.mp3";
        } else {
          camera(reply["device_action"]);
        }
      } else {
        reply["action"] = "reply";
        reply["reply"]["id"] = "reply-1";
        reply["reply"]["text"] = "こんにちは";
      }
    } else if (type == "playback_completed" || type == "action_completed") {
      assert(request.size() == 6);
      assert(request["status"] == "succeeded" || request["status"] == "failed");
      if (type == "playback_completed") assert(request["reply_id"] == "reply-1");
      else assert(request["action_id"] == "action-1");
    } else {
      assert(request.size() == 4);
    }
    if (mutate) mutate(reply);
    if (rawResponse.empty()) serializeJson(reply, response);
    else response = rawResponse;
    return status;
  }
  std::string field(size_t index, const char* name) const {
    ArduinoJson::JsonDocument doc;
    assert(!deserializeJson(doc, requests.at(index)));
    return doc[name].as<std::string>();
  }
};

static void normalAndAcknowledgements() {
  Transport transport;
  Session session(transport, makeId, 2);
  assert(session.stop() && transport.requests.empty());
  assert(session.start() && session.enabled());
  assert(session.phase() == Phase::Listening);
  assert(!session.pendingRetry() && !session.needsRestart());
  const auto id = session.sessionId();
  for (bool succeeded : {true, false}) {
    assert(session.speech("こんにちは") == Outcome::Reply);
    assert(session.phase() == Phase::PlaybackPending);
    assert(session.replyText() == "こんにちは");
    assert(session.deviceAction().actionId.empty());
    assert(!session.faceTouch() && !session.actionCompleted(true));
    assert(session.speech("blocked") == Outcome::Failed);
    assert(session.playbackCompleted(succeeded));
    assert(transport.field(transport.requests.size() - 1, "status") == (succeeded ? "succeeded" : "failed"));
    assert(session.phase() == Phase::Listening && session.replyText().empty());
    assert(!session.playbackCompleted());
    for (bool audio : {false, true}) {
      transport.perform = true;
      transport.audio = audio;
      assert(session.speech("実行してください") == Outcome::Perform);
      assert(session.phase() == Phase::ActionPending && session.enabled());
      assert(session.replyText().empty());
      const auto& action = session.deviceAction();
      assert(action.actionId == "action-1");
      assert(action.type == (audio ? "audio_playback" : "camera_capture"));
      if (audio) {
        assert(action.audioUrl == "https://example.test/song.mp3");
        assert(action.transcript.empty());
      } else {
        assert(action.cameraMode == "transient" && action.cameraRequester == "user");
        assert(action.transcript == "見てください" && action.speaker == "master");
        assert(action.speakerLabel.empty() && action.announceEndpoint == "camera_start-1");
      }
      const auto count = transport.requests.size();
      assert(!session.faceTouch() && !session.playbackCompleted());
      assert(session.speech("blocked") == Outcome::Failed);
      assert(transport.requests.size() == count);
      assert(session.actionCompleted(succeeded));
      assert(transport.field(transport.requests.size() - 1, "status") == (succeeded ? "succeeded" : "failed"));
      assert(session.phase() == Phase::Listening && session.deviceAction().actionId.empty());
      assert(!session.actionCompleted(true));
      transport.perform = false;
    }
  }
  assert(session.faceTouch());
  assert(session.stop() && session.phase() == Phase::Off && session.sessionId().empty());
  assert(session.start() && session.sessionId() != id);
}

static void sameRequestRetries() {
  for (int status : {-1, 0, 408, 429, 500, 503, 599}) {
    for (bool perform : {false, true}) {
      Transport transport;
      Session session(transport, makeId, 2);
      assert(session.start());
      transport.perform = perform;
      transport.status = status;
      assert(session.speech("一度だけ実行") == Outcome::Retry);
      assert(session.phase() == Phase::Recovering && session.pendingRetry());
      assert(!session.enabled() && !session.needsRestart());
      const auto body = transport.requests.back();
      const auto count = transport.requests.size();
      const auto ids = nextId;
      assert(!session.start() && !session.faceTouch() && !session.actionCompleted(true));
      assert(!session.playbackCompleted() && session.speech("discard") == Outcome::Failed);
      assert(transport.requests.size() == count);
      assert(session.retryPending() == Outcome::Retry);
      assert(transport.requests.back() == body && nextId == ids);
      transport.status = 200;
      assert(session.retryPending() == (perform ? Outcome::Perform : Outcome::Reply));
      assert(transport.requests.back() == body && nextId == ids);
      assert(!session.pendingRetry());
      // The owner can execute the result once; retry cannot deliver it twice.
      assert(session.retryPending() == Outcome::Failed);
      assert(transport.requests.size() == count + 2);
      for (bool succeeded : {false, true}) {
        if (succeeded) {
          assert(session.speech("次の処理") == (perform ? Outcome::Perform : Outcome::Reply));
        }
        transport.status = status;
        assert(!(perform ? session.actionCompleted(succeeded) : session.playbackCompleted(succeeded)));
        assert(session.pendingRetry() && session.replyText().empty() && session.deviceAction().actionId.empty());
        const auto acknowledgement = transport.requests.back();
        assert(transport.field(transport.requests.size() - 1, "status") == (succeeded ? "succeeded" : "failed"));
        transport.status = 200;
        assert(session.retryPending() == Outcome::Listen);  // Never Reply/Perform.
        assert(transport.requests.back() == acknowledgement);
        assert(!session.pendingRetry() && session.retryPending() == Outcome::Failed);
      }
    }
  }
  Transport transport;
  Session session(transport, makeId, 2);
  transport.status = 503;
  assert(!session.start() && session.pendingRetry());
  const auto startBody = transport.requests.back();
  transport.status = 200;
  assert(session.retryPending() == Outcome::Listen && transport.requests.back() == startBody);
  transport.status = 408;
  assert(!session.faceTouch() && session.pendingRetry());
  const auto faceBody = transport.requests.back();
  transport.status = 200;
  assert(session.retryPending() == Outcome::Listen && transport.requests.back() == faceBody);
  transport.status = 500;
  assert(session.speech("cancel me") == Outcome::Retry);
  const auto oldSessionId = session.sessionId();
  transport.observer = [&] { assert(session.phase() == Phase::Off && !session.pendingRetry()); };
  assert(!session.stop());
  transport.observer = nullptr;
  assert(session.phase() == Phase::Off && !session.pendingRetry() && !session.needsRestart());
  assert(session.replyText().empty() && session.deviceAction().actionId.empty() && session.sessionId().empty());
  const auto count = transport.requests.size();
  assert(session.retryPending() == Outcome::Failed && transport.requests.size() == count);
  transport.status = 200;
  assert(session.start() && session.sessionId() != oldSessionId);
}

static void typedConflictsAndMalformed() {
  for (const std::string code : {"event_processing", "speech_processing", "session_not_started", "session_closed",
       "event_id_reused", "reply_playback_pending", "device_action_pending", "reply_id_mismatch", "action_id_mismatch", "unknown"}) {
    Transport transport;
    Session session(transport, makeId, 2);
    assert(session.start());
    transport.status = 409;
    transport.mutate = [&](ArduinoJson::JsonDocument& doc) { doc.clear(); doc["error"]["code"] = code; };
    const bool retry = code == "event_processing" || code == "speech_processing";
    const bool restart = code == "session_not_started" || code == "session_closed";
    assert(session.speech("test") == (retry ? Outcome::Retry : Outcome::Failed));
    assert(session.pendingRetry() == retry && session.needsRestart() == restart);
    const auto pending = transport.requests.back();
    transport.status = 200;
    transport.mutate = nullptr;
    if (retry) {
      assert(session.retryPending() == Outcome::Reply && transport.requests.back() == pending);
    } else {
      const auto count = transport.requests.size();
      const auto oldSessionId = session.sessionId();
      assert(session.phase() == Phase::Fault && !session.enabled());
      assert(!session.start() && session.retryPending() == Outcome::Failed);
      assert(transport.requests.size() == count);
      session.abandon();
      assert(!session.needsRestart() && session.start() && session.sessionId() != oldSessionId);
      assert(transport.field(count, "type") == "start");
    }
  }
  const std::vector<std::function<void(ArduinoJson::JsonDocument&)>> invalid = {
    [](ArduinoJson::JsonDocument& d) { d["session_id"] = "old-session"; },
    [](ArduinoJson::JsonDocument& d) { d["event_id"] = "old-event"; },
    [](ArduinoJson::JsonDocument& d) { d["schema_version"] = 1; },
    [](ArduinoJson::JsonDocument& d) { d["ok"] = 1; },
    [](ArduinoJson::JsonDocument& d) { d.remove("device_action"); },
    [](ArduinoJson::JsonDocument& d) { camera(d["device_action"]); },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["id"] = " bad"; },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["text"] = "[[VISION_TRANSIENT_TRIGGER]]"; },
  };
  for (const auto& mutation : invalid) {
    Transport transport;
    Session session(transport, makeId, 2);
    assert(session.start());
    transport.mutate = mutation;
    assert(session.speech("reject") == Outcome::Failed);
    assert(session.phase() == Phase::Fault && !session.pendingRetry() && !session.needsRestart());
  }
  for (int status : {200, 409}) {
    Transport transport;
    Session session(transport, makeId, 2);
    assert(session.start());
    transport.status = status;
    transport.rawResponse = "{broken";
    assert(session.speech("reject malformed") == Outcome::Failed && !session.pendingRetry());
  }
  Transport transport;
  Session session(transport, makeId, 2);
  assert(session.start());
  transport.status = 409;
  transport.mutate = [](ArduinoJson::JsonDocument& d) {
    d["event_id"] = "old-event";
    d["error"]["code"] = "event_processing";
  };
  assert(session.speech("do not retry a mismatched error") == Outcome::Failed && !session.pendingRetry());
}

static void actionValidation() {
  const std::vector<std::function<void(ArduinoJson::JsonDocument&)>> invalid = {
    [](ArduinoJson::JsonDocument& d) { d["reply"]["id"] = "reply-1"; d["reply"]["text"] = "no dual output"; },
    [](ArduinoJson::JsonDocument& d) { d.remove("reply"); },
    [](ArduinoJson::JsonDocument& d) { d["state"] = "quiet"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["action_id"] = ""; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["action_id"] = japanese(65); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["action_id"] = "id\n"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["type"] = "execute_command"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["camera_mode"] = "unknown"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["camera_requester"] = "other"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["speaker"] = ""; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["speaker"] = japanese(65); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"].remove("speaker_label"); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["speaker_label"] = japanese(129); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"].remove("transcript"); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["transcript"] = japanese(1001); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["announce_endpoint"] = "../secret"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["announce_endpoint"] = "a/b"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["announce_endpoint"] = "a\\b"; },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["announce_endpoint"] = std::string(65, 'a'); },
    [](ArduinoJson::JsonDocument& d) { d["device_action"]["announce_endpoint"] = 1; },
  };
  for (const auto& mutation : invalid) {
    Transport transport;
    Session session(transport, makeId, 2);
    assert(session.start());
    transport.perform = true;
    transport.mutate = mutation;
    assert(session.speech("reject unsafe action") == Outcome::Failed);
    assert(session.deviceAction().actionId.empty() && !session.pendingRetry());
  }
  Transport transport;
  Session session(transport, makeId, 2);
  assert(session.start());
  transport.perform = true;
  transport.mutate = [](ArduinoJson::JsonDocument& d) {
    auto a = d["device_action"];
    a["speaker"] = japanese(64);
    a["speaker_label"] = japanese(128);
    a["transcript"] = japanese(1000);
    a.remove("announce_endpoint");
  };
  assert(session.speech("allow exact limits") == Outcome::Perform);
  assert(session.deviceAction().announceEndpoint.empty());
  transport.mutate = nullptr;
  assert(session.actionCompleted(true));
  for (const std::string url : {"", "file:///tmp/a", "ftp://example.test/a", "http://", "http:///a",
       "https://user:password@example.test/a", "https://example.test/a\r\nHost: evil"}) {
    transport.audio = true;
    transport.mutate = [&](ArduinoJson::JsonDocument& d) { d["device_action"]["audio_url"] = url; };
    assert(session.speech("reject url") == Outcome::Failed);
    session.abandon();
    transport.mutate = nullptr;
    assert(session.start());
  }
  assert(Session::validId(japanese(64)) && !Session::validId(japanese(65)));
  assert(!Session::validId(" id") && !Session::validId("id\t") && !Session::validId("a\nb"));
  assert(!Session::validId(std::string("a\0b", 3)));
  assert(Session::validEndpoint("") && Session::validEndpoint("a-Z_09"));
  assert(!Session::validEndpoint("a..b") && !Session::validEndpoint("日本"));
  assert(Session::validAudioUrl("http://127.0.0.1:5050/audio/@path"));
  assert(Session::validAudioUrl("https://[::1]:5050/audio"));
  const std::string prefix = "https://example.test/";
  assert(Session::validAudioUrl(prefix + std::string(2048 - prefix.size(), 'a')));
  assert(!Session::validAudioUrl(prefix + std::string(2049 - prefix.size(), 'a')));
  assert(!Session::validAudioUrl(std::string("https://x/\0hidden", 17)));
}

static void touchOneShot() {
  for (bool succeeded : {true, false}) {
    for (bool retry : {true, false}) {
      Transport transport;
      transport.oneShot = true;
      Session session(transport, makeId, 2);
      ArduinoJson::JsonDocument envelope;
      envelope["action_schema_version"] = 2;
      envelope["session_id"] = "touch-session";
      envelope["event_id"] = "touch-event";
      envelope["action"] = "perform";
      envelope["device_action"] = nullptr;
      camera(envelope["device_action"]);
      assert(session.adoptTouchAction("touch-session", "touch-event", envelope.as<ArduinoJson::JsonVariantConst>()));
      assert(transport.requests.empty() && session.phase() == Phase::ActionPending);
      assert(!session.adoptTouchAction("other-session", "other-event", envelope.as<ArduinoJson::JsonVariantConst>()));
      assert(session.sessionId() == "touch-session" && session.deviceAction().actionId == "action-1");
      transport.status = retry ? 503 : 200;
      assert(session.actionCompleted(succeeded) == !retry);
      assert(transport.field(0, "status") == (succeeded ? "succeeded" : "failed"));
      if (retry) {
        assert(session.pendingRetry());
        transport.status = 200;
        assert(session.retryPending() == Outcome::Stopped);
        assert(transport.requests[0] == transport.requests[1]);
      }
      assert(session.phase() == Phase::Off && !session.enabled() && !session.pendingRetry());
      assert(session.sessionId().empty() && session.deviceAction().actionId.empty());
      assert(!session.actionCompleted(true) && session.retryPending() == Outcome::Failed);
    }
  }
  for (const char* wrongField : {"session_id", "event_id", "action_schema_version"}) {
    Transport transport;
    Session session(transport, makeId, 2);
    ArduinoJson::JsonDocument envelope;
    envelope["action_schema_version"] = 2;
    envelope["session_id"] = "touch-session";
    envelope["event_id"] = "touch-event";
    envelope["action"] = "perform";
    envelope["device_action"] = nullptr;
    camera(envelope["device_action"]);
    envelope[wrongField] = "wrong";
    assert(!session.adoptTouchAction("touch-session", "touch-event", envelope.as<ArduinoJson::JsonVariantConst>()));
    assert(transport.requests.empty() && session.phase() == Phase::Fault);
  }
  Transport transport;
  Session session(transport, makeId, 2);
  ArduinoJson::JsonDocument envelope;
  envelope["action_schema_version"] = 2;
  envelope["session_id"] = "touch-session";
  envelope["event_id"] = "touch-event";
  envelope["action"] = "perform";
  envelope["device_action"] = nullptr;
  camera(envelope["device_action"]);
  assert(session.adoptTouchAction("touch-session", "touch-event", envelope.as<ArduinoJson::JsonVariantConst>()));
  // Always-style active/listen is not a valid one-shot completion.
  assert(!session.actionCompleted(true) && session.phase() == Phase::Fault);
}

int main() {
  {
    Transport transport;
    Session session(transport, makeId, 2);
    assert(session.start());
    assert(session.speech(japanese(1000)) == Outcome::Reply);
    assert(transport.field(1, "text") == japanese(1000));
    assert(session.playbackCompleted(true));
    const auto sent = transport.requests.size();
    assert(session.speech(japanese(1001)) == Outcome::Failed);
    assert(transport.requests.size() == sent);
  }
  normalAndAcknowledgements();
  sameRequestRetries();
  typedConflictsAndMalformed();
  actionValidation();
  touchOneShot();
}

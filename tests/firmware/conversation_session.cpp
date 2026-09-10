#include <cassert>
#include <functional>
#include <vector>
#include "share/ConversationSession.h"

using Session = stackchan::ConversationSession;
using Outcome = Session::Outcome;
using Phase = Session::Phase;

static unsigned nextId = 0;
static std::string makeId() { return "test-" + std::to_string(++nextId); }

struct Transport : stackchan::ConversationTransport {
  std::vector<std::string> requests;
  int status = 200;
  std::function<void(ArduinoJson::JsonDocument&)> mutate;
  int post(const std::string& body, std::string& response) override {
    requests.push_back(body);
    ArduinoJson::JsonDocument request, reply;
    assert(!deserializeJson(request, body));
    const std::string type = request["type"];
    assert(request.size() == ((type == "speech" || type == "playback_completed") ? 5 : 4));
    reply["ok"] = true;
    reply["schema_version"] = request["schema_version"];
    reply["session_id"] = request["session_id"];
    reply["event_id"] = request["event_id"];
    reply["state"] = type == "stop" ? "closed" : "active";
    reply["action"] = type == "stop" ? "stopped" : "listen";
    reply["reply"] = nullptr;
    if (type == "speech") {
      reply["action"] = "reply";
      reply["reply"]["id"] = "reply-1";
      reply["reply"]["text"] = "こんにちは";
    }
    if (type == "playback_completed") assert(request["reply_id"] == "reply-1");
    if (mutate) mutate(reply);
    serializeJson(reply, response);
    return status;
  }
  std::string field(size_t index, const char* name) const {
    ArduinoJson::JsonDocument doc;
    assert(!deserializeJson(doc, requests.at(index)));
    return doc[name].as<std::string>();
  }
};

int main() {
  Transport transport;
  Session session(transport, makeId);
  assert(session.stop() && transport.requests.empty());
  assert(!session.faceTouch() && !session.playbackCompleted());
  assert(session.speech("not started") == Outcome::Failed);
  assert(transport.requests.empty());
  assert(session.start() && session.enabled());
  assert(!session.start() && transport.requests.size() == 1);
  assert(session.speech("あのね") == Outcome::Reply);
  assert(session.phase() == Phase::PlaybackPending);
  assert(session.replyText() == "こんにちは");
  assert(session.speech("too early") == Outcome::Failed);
  assert(!session.faceTouch());
  assert(transport.requests.size() == 2);  // Nothing passes the playback barrier.
  assert(session.playbackCompleted() && session.phase() == Phase::Listening);
  assert(!session.playbackCompleted() && transport.requests.size() == 3);
  assert(session.faceTouch());
  assert(session.stop() && !session.enabled() && session.replyText().empty());
  assert(session.start());
  assert(transport.field(0, "session_id") != transport.field(5, "session_id"));
  for (size_t i = 1; i < transport.requests.size(); ++i) {
    assert(transport.field(i, "event_id") != transport.field(i - 1, "event_id"));
  }
  assert(transport.field(1, "text") == "あのね");

  transport.mutate = [](ArduinoJson::JsonDocument& doc) {
    doc["state"] = "quiet";
    doc["action"] = "wait";
    doc["reply"] = nullptr;
  };
  assert(session.speech("wait quietly") == Outcome::Wait && session.quiet());
  assert(session.replyText().empty());
  transport.mutate = nullptr;
  assert(session.faceTouch() && !session.quiet());
  assert(session.stop());

  // Bad envelopes, unsupported actions and failed transport stop capture.
  const std::vector<std::function<void(ArduinoJson::JsonDocument&)>> bad = {
    [](ArduinoJson::JsonDocument& d) { d["ok"] = 1; },
    [](ArduinoJson::JsonDocument& d) { d["schema_version"] = "1"; },
    [](ArduinoJson::JsonDocument& d) { d["schema_version"] = 2; },
    [](ArduinoJson::JsonDocument& d) { d["session_id"] = "wrong"; },
    [](ArduinoJson::JsonDocument& d) { d["event_id"] = "wrong"; },
    [](ArduinoJson::JsonDocument& d) { d["state"] = "unknown"; },
    [](ArduinoJson::JsonDocument& d) { d["action"] = "unknown"; },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["id"] = ""; },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["text"] = 7; },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["text"] = std::string("a\0b", 3); },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["text"] = "[[VISION_TRIGGER]]"; },
    [](ArduinoJson::JsonDocument& d) { d["stackchan_action"] = "camera_trigger"; },
    [](ArduinoJson::JsonDocument& d) { d["reply"]["text"] = std::string(Session::MaxResponseBytes, 'x'); },
  };
  for (const auto& mutation : bad) {
    Transport broken;
    Session s(broken, makeId);
    assert(s.start());
    broken.mutate = mutation;
    assert(s.speech("test") == Outcome::Failed && s.phase() == Phase::Fault);
    assert(!s.enabled() && s.replyText().empty());
    assert(!s.playbackCompleted());
    assert(s.speech("no auto retry") == Outcome::Failed);
    assert(broken.requests.size() == 2);
    broken.status = 500;
    assert(!s.stop() && s.phase() == Phase::Off);
  }
  for (int status : {-1, 201, 204, 400, 401, 409, 429, 500}) {
    Transport broken;
    Session s(broken, makeId);
    assert(s.start());
    assert(s.speech("test") == Outcome::Reply);
    broken.status = status;
    assert(!s.playbackCompleted() && !s.enabled());
    assert(broken.requests.size() == 3);
    assert(!s.stop() && s.phase() == Phase::Off);
  }
  // Unicode limit is characters, not UTF-8 bytes. Never silently truncate.
  Transport unicode;
  Session s(unicode, makeId);
  assert(s.start());
  std::string text;
  for (int i = 0; i < 1000; ++i) text += "あ";
  assert(s.speech(text) == Outcome::Reply);
  assert(unicode.field(1, "text") == text);
  assert(s.playbackCompleted());
  assert(s.speech(text + "あ") == Outcome::Failed);
  assert(unicode.requests.size() == 3);
  assert(s.stop() && s.start());
  assert(s.speech(std::string("a\0b", 3)) == Outcome::Failed);
}

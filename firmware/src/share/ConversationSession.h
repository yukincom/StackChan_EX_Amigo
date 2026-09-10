#pragma once

#include <ArduinoJson.h>
#include <cstring>
#include <string>
#ifdef ARDUINO
#include "SpiRamJsonDocument.h"
#endif

namespace stackchan {
#ifdef ARDUINO
using EventJsonDocument = SpiRamJsonDocument;
#else
using EventJsonDocument = ArduinoJson::DynamicJsonDocument;
#endif

class ConversationTransport {
 public:
  virtual ~ConversationTransport() = default;
  // Return HTTP status; a non-positive result means a transport failure.
  virtual int post(const std::string& body, std::string& response) = 0;
};

// Synchronous v1 client. No conversation history, queues, background tasks,
// automatic restart or audio replay. IDs have no device/user identity in them.
class ConversationSession {
 public:
  static constexpr size_t MaxResponseBytes = 16 * 1024;
  enum class Phase { Off, Listening, PlaybackPending, Fault };
  enum class Outcome { Failed, Listen, Wait, Reply, Stopped };
  enum class Error { None, Transport, Protocol, UnsupportedAction, Input };
  using IdFactory = std::string (*)();

  ConversationSession(ConversationTransport& transport, IdFactory makeId)
      : transport_(transport), makeId_(makeId) {}

  bool start() {
    if (phase_ != Phase::Off) return false;
    sessionId_ = makeId_();
    replyId_.clear();
    replyText_.clear();
    quiet_ = false;
    return send("start") == Outcome::Listen;
  }
  bool stop() {
    // Local capture is off even if the server is unreachable.
    const bool hadSession = !sessionId_.empty();
    phase_ = Phase::Off;
    bool ok = !hadSession || send("stop") == Outcome::Stopped;
    phase_ = Phase::Off;
    sessionId_.clear();
    replyId_.clear();
    replyText_.clear();
    quiet_ = false;
    return ok;
  }
  bool faceTouch() {
    return phase_ == Phase::Listening && send("face_touch") == Outcome::Listen;
  }
  Outcome speech(const std::string& text) {
    if (phase_ != Phase::Listening) return Outcome::Failed;
    size_t characters = 0;
    for (unsigned char c : text) {
      if ((c & 0xc0) != 0x80) ++characters;
    }
    if (text.empty() || characters > 1000 || text.find('\0') != std::string::npos) {
      return fail(Error::Input);
    }
    return send("speech", text);
  }
  bool playbackCompleted() {
    return phase_ == Phase::PlaybackPending
        && send("playback_completed", replyId_) == Outcome::Listen;
  }

  Phase phase() const { return phase_; }
  bool enabled() const { return phase_ == Phase::Listening || phase_ == Phase::PlaybackPending; }
  bool quiet() const { return quiet_; }
  Error error() const { return error_; }
  const std::string& replyText() const { return replyText_; }

 private:
  static bool validId(const std::string& id) {
    if (id.empty() || id.size() > 64 || id.front() == ' ' || id.back() == ' ') return false;
    return id.find_first_of("\r\n") == std::string::npos
        && id.find('\0') == std::string::npos;
  }
  Outcome fail(Error error) {
    error_ = error;
    phase_ = Phase::Fault;
    replyId_.clear();
    replyText_.clear();
    return Outcome::Failed;
  }
  Outcome send(const char* type, const std::string& value = {}) {
    error_ = Error::None;
    const std::string eventId = makeId_();
    if (!validId(sessionId_) || !validId(eventId)) return fail(Error::Input);
    EventJsonDocument request(1024);
    request["schema_version"] = 1;
    request["session_id"] = sessionId_;
    request["event_id"] = eventId;
    request["type"] = type;
    if (std::strcmp(type, "speech") == 0) request["text"] = value;
    if (std::strcmp(type, "playback_completed") == 0) request["reply_id"] = value;
    if (request.overflowed()) return fail(Error::Input);
    std::string body;
    serializeJson(request, body);
    if (body.size() > 8192) return fail(Error::Input);
    std::string response;
    if (transport_.post(body, response) != 200) return fail(Error::Transport);
    if (response.size() > MaxResponseBytes) return fail(Error::Protocol);
    EventJsonDocument doc(4096);
    if (deserializeJson(doc, response) || doc.overflowed()
        || !doc["ok"].is<bool>() || !doc["ok"].as<bool>()
        || !doc["schema_version"].is<int>() || doc["schema_version"].as<int>() != 1
        || !doc["session_id"].is<const char*>() || doc["session_id"].as<std::string>() != sessionId_
        || !doc["event_id"].is<const char*>() || doc["event_id"].as<std::string>() != eventId) {
      return fail(Error::Protocol);
    }
    // Camera markers / actions need their own completion contract. Never read
    // an internal action marker aloud or acknowledge it as played speech.
    if (!doc["stackchan_action"].isNull()) return fail(Error::UnsupportedAction);
    const std::string state = doc["state"] | "";
    const std::string action = doc["action"] | "";
    if (std::strcmp(type, "stop") == 0) {
      if (state != "closed" || action != "stopped" || !doc["reply"].isNull()) return fail(Error::Protocol);
      return Outcome::Stopped;
    }
    if (state != "active" && state != "quiet") return fail(Error::Protocol);
    quiet_ = state == "quiet";
    if (std::strcmp(type, "speech") == 0 && action == "reply") {
      if (!doc["reply"]["id"].is<const char*>() || !doc["reply"]["text"].is<const char*>()) return fail(Error::Protocol);
      replyId_ = doc["reply"]["id"].as<std::string>();
      replyText_ = doc["reply"]["text"].as<std::string>();
      if (!validId(replyId_) || replyText_.empty()
          || replyText_.find('\0') != std::string::npos) return fail(Error::Protocol);
      if (replyText_.find("[[VISION_") != std::string::npos) return fail(Error::UnsupportedAction);
      phase_ = Phase::PlaybackPending;
      return Outcome::Reply;
    }
    const bool speechEvent = std::strcmp(type, "speech") == 0;
    if (!doc["reply"].isNull() || (action != "listen" && !(speechEvent && action == "wait"))) return fail(Error::Protocol);
    // Control acknowledgements must not silently retain quiet/closed state.
    if (!speechEvent && state != "active") return fail(Error::Protocol);
    phase_ = Phase::Listening;
    replyId_.clear();
    replyText_.clear();
    return action == "wait" ? Outcome::Wait : Outcome::Listen;
  }

  ConversationTransport& transport_;
  IdFactory makeId_;
  Phase phase_ = Phase::Off;
  Error error_ = Error::None;
  bool quiet_ = false;
  std::string sessionId_;
  std::string replyId_;
  std::string replyText_;
};
}  // namespace stackchan

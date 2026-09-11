#pragma once

#include <ArduinoJson.h>
#include <cctype>
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
  // Preserve response bodies on non-200 status too, for typed v2 conflicts.
  virtual int post(const std::string& body, std::string& response) = 0;
};

// Synchronous client: no capture/replay loop, history or automatic restart.
// V2 retries preserve exactly the pending request body and event identifier.
class ConversationSession {
 public:
  static constexpr size_t MaxResponseBytes = 16 * 1024;
  enum class Phase { Off, Listening, PlaybackPending, Fault, ActionPending, Recovering };
  enum class Outcome { Failed, Listen, Wait, Reply, Stopped, Perform, Retry };
  enum class Error { None, Transport, Protocol, UnsupportedAction, Input };
  using IdFactory = std::string (*)();

  struct DeviceAction {
    std::string actionId;
    std::string type;
    std::string cameraMode;
    std::string cameraRequester;
    std::string transcript;
    std::string speaker;
    std::string speakerLabel;
    std::string announceEndpoint;
    std::string audioUrl;
  };

  ConversationSession(ConversationTransport& transport, IdFactory makeId,
                      int schemaVersion = 1)
      : transport_(transport), makeId_(makeId), schemaVersion_(schemaVersion) {}

  bool start() {
    if (phase_ != Phase::Off) return false;
    abandon();
    sessionId_ = makeId_();
    return send("start") == Outcome::Listen;
  }

  bool stop() {
    // An explicit stop cancels a pending retry immediately. Even a failed
    // stop must not schedule another event or re-enable local capture.
    const bool hadSession = !sessionId_.empty();
    phase_ = Phase::Off;
    clearPending();
    const bool ok = !hadSession || send("stop", {}, true, false) == Outcome::Stopped;
    const Error error = error_;
    abandon();
    error_ = error;
    return ok;
  }

  bool faceTouch() {
    return phase_ == Phase::Listening && send("face_touch") == Outcome::Listen;
  }

  Outcome speech(const std::string& text) {
    if (phase_ != Phase::Listening) return Outcome::Failed;
    if (!validText(text, 1000, false)) return fail(Error::Input);
    return send("speech", text);
  }

  bool playbackCompleted(bool succeeded = true) {
    if (phase_ != Phase::PlaybackPending) return false;
    // V1 has no failed-playback acknowledgement; never claim it succeeded.
    if (schemaVersion_ == 1 && !succeeded) {
      fail(Error::Input);
      return false;
    }
    return send("playback_completed", replyId_, succeeded) == Outcome::Listen;
  }

  bool actionCompleted(bool succeeded) {
    if (schemaVersion_ != 2 || phase_ != Phase::ActionPending) return false;
    const Outcome expected = oneShot_ ? Outcome::Stopped : Outcome::Listen;
    return send("action_completed", deviceAction_.actionId, succeeded) == expected;
  }

  Outcome retryPending() {
    if (!pendingRetry()) return Outcome::Failed;
    return postPending(true);
  }

  bool pendingRetry() const {
    return phase_ == Phase::Recovering && !pendingBody_.empty();
  }
  bool needsRestart() const { return needsRestart_; }

  // Only the owner decides whether to begin a new session. Old transcript,
  // result, acknowledgement and retry identities must never survive this.
  void abandon() {
    phase_ = Phase::Off;
    error_ = Error::None;
    quiet_ = false;
    oneShot_ = false;
    needsRestart_ = false;
    sessionId_.clear();
    clearResult();
    clearPending();
  }

  bool adoptTouchAction(const std::string& expectedSessionId,
                        const std::string& expectedEventId,
                        ArduinoJson::JsonVariantConst envelope) {
    // Never overwrite a live always session or an unresolved one-shot action.
    if (phase_ != Phase::Off) return false;
    if (schemaVersion_ != 2 || !validId(expectedSessionId) || !validId(expectedEventId)
        || !envelope.is<ArduinoJson::JsonObjectConst>()
        || !envelope["action_schema_version"].is<int>()
        || envelope["action_schema_version"].as<int>() != 2
        || !matchesString(envelope["session_id"], expectedSessionId)
        || !matchesString(envelope["event_id"], expectedEventId)
        || !matchesString(envelope["action"], "perform")
        || !envelope["reply"].isNull()) {
      fail(Error::Protocol);
      return false;
    }
    DeviceAction action;
    if (!parseDeviceAction(envelope["device_action"], action)) {
      fail(Error::UnsupportedAction);
      return false;
    }
    abandon();
    sessionId_ = expectedSessionId;
    deviceAction_ = action;
    oneShot_ = true;
    phase_ = Phase::ActionPending;
    return true;
  }

  Phase phase() const { return phase_; }
  bool enabled() const {
    return phase_ == Phase::Listening || phase_ == Phase::PlaybackPending
        || phase_ == Phase::ActionPending;
  }
  bool quiet() const { return quiet_; }
  Error error() const { return error_; }
  const std::string& sessionId() const { return sessionId_; }
  const std::string& replyText() const { return replyText_; }
  const DeviceAction& deviceAction() const { return deviceAction_; }

  // Shared wire validators are also used by correlated device push handlers.
  static size_t characters(const std::string& text) {
    size_t count = 0;
    for (unsigned char c : text) {
      if ((c & 0xc0) != 0x80) ++count;
    }
    return count;
  }
  static bool validText(const std::string& text, size_t maximum, bool emptyAllowed) {
    return (emptyAllowed || !text.empty()) && characters(text) <= maximum
        && text.find('\0') == std::string::npos;
  }
  static bool validId(const std::string& id) {
    return validText(id, 64, false)
        && !std::isspace(static_cast<unsigned char>(id.front()))
        && !std::isspace(static_cast<unsigned char>(id.back()))
        && id.find_first_of("\r\n") == std::string::npos;
  }
  static bool matchesString(ArduinoJson::JsonVariantConst value,
                            const std::string& expected) {
    return value.is<const char*>() && value.as<std::string>() == expected;
  }
  static bool readText(ArduinoJson::JsonVariantConst value, std::string& output,
                       size_t maximum, bool emptyAllowed) {
    if (!value.is<const char*>()) return false;
    output = value.as<std::string>();
    return validText(output, maximum, emptyAllowed);
  }
  static bool validEndpoint(const std::string& endpoint) {
    if (!validText(endpoint, 64, true)) return false;
    for (unsigned char c : endpoint) {
      if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
            || (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    }
    return true;
  }
  static bool validAudioUrl(const std::string& url) {
    if (!validText(url, 2048, false) || url.find_first_of("\r\n") != std::string::npos) return false;
    const size_t begin = url.compare(0, 7, "http://") == 0 ? 7
        : url.compare(0, 8, "https://") == 0 ? 8 : 0;
    if (begin == 0) return false;
    const size_t end = url.find_first_of("/?#", begin);
    const std::string authority = url.substr(begin, end == std::string::npos ? end : end - begin);
    return !authority.empty() && authority.find('@') == std::string::npos;
  }

 private:
  static bool parseDeviceAction(ArduinoJson::JsonVariantConst value, DeviceAction& action) {
    if (!value.is<ArduinoJson::JsonObjectConst>()
        || !readText(value["action_id"], action.actionId, 64, false)
        || !validId(action.actionId) || !value["type"].is<const char*>()) return false;
    action.type = value["type"].as<std::string>();
    if (action.type == "audio_playback") {
      return readText(value["audio_url"], action.audioUrl, 2048, false)
          && validAudioUrl(action.audioUrl);
    }
    if (action.type != "camera_capture") return false;
    if (!value["camera_mode"].is<const char*>()
        || !value["camera_requester"].is<const char*>()) return false;
    action.cameraMode = value["camera_mode"].as<std::string>();
    action.cameraRequester = value["camera_requester"].as<std::string>();
    if ((action.cameraMode != "archive" && action.cameraMode != "transient")
        || (action.cameraRequester != "user" && action.cameraRequester != "ai")
        || !readText(value["transcript"], action.transcript, 1000, true)
        || !readText(value["speaker"], action.speaker, 64, false)
        || !readText(value["speaker_label"], action.speakerLabel, 128, true)) return false;
    const auto object = value.as<ArduinoJson::JsonObjectConst>();
    if (object.containsKey("announce_endpoint")) {
      if (!readText(value["announce_endpoint"], action.announceEndpoint, 64, true)
          || !validEndpoint(action.announceEndpoint)) return false;
    }
    return true;
  }

  void clearResult() {
    replyId_.clear();
    replyText_.clear();
    deviceAction_ = DeviceAction{};
  }
  void clearPending() {
    pendingBody_.clear();
    pendingEventId_.clear();
    pendingType_.clear();
  }
  Outcome fail(Error error, bool restart = false) {
    error_ = error;
    phase_ = Phase::Fault;
    needsRestart_ = restart;
    clearResult();
    clearPending();
    return Outcome::Failed;
  }
  Outcome recover(Error error) {
    error_ = error;
    phase_ = Phase::Recovering;
    needsRestart_ = false;
    clearResult();
    return Outcome::Retry;
  }

  Outcome send(const char* type, const std::string& value = {},
               bool succeeded = true, bool allowRetry = true) {
    error_ = Error::None;
    needsRestart_ = false;
    const std::string eventId = makeId_();
    if ((schemaVersion_ != 1 && schemaVersion_ != 2)
        || !validId(sessionId_) || !validId(eventId)) return fail(Error::Input);
    EventJsonDocument request(1024);
    request["schema_version"] = schemaVersion_;
    request["session_id"] = sessionId_;
    request["event_id"] = eventId;
    request["type"] = type;
    if (std::strcmp(type, "speech") == 0) request["text"] = value;
    if (std::strcmp(type, "playback_completed") == 0) request["reply_id"] = value;
    if (std::strcmp(type, "action_completed") == 0) request["action_id"] = value;
    if (schemaVersion_ == 2 && (std::strcmp(type, "playback_completed") == 0
        || std::strcmp(type, "action_completed") == 0)) {
      request["status"] = succeeded ? "succeeded" : "failed";
    }
    if (request.overflowed()) return fail(Error::Input);
    clearPending();
    serializeJson(request, pendingBody_);
    if (pendingBody_.size() > 8192) return fail(Error::Input);
    pendingEventId_ = eventId;
    pendingType_ = type;
    return postPending(allowRetry);
  }

  Outcome postPending(bool allowRetry) {
    std::string response;
    const int status = transport_.post(pendingBody_, response);
    if (response.size() > MaxResponseBytes) return fail(Error::Protocol);
    if (status != 200) {
      if (schemaVersion_ == 2 && allowRetry) {
        if (status <= 0 || status == 408 || status == 429 || (status >= 500 && status <= 599)) {
          return recover(Error::Transport);
        }
        if (status == 409) {
          EventJsonDocument doc(4096);
          if (deserializeJson(doc, response) || doc.overflowed()
              || !doc["error"]["code"].is<const char*>()) return fail(Error::Protocol);
          // Error envelopes may omit IDs; supplied IDs must still match.
          const auto object = doc.as<ArduinoJson::JsonObjectConst>();
          if ((object.containsKey("session_id") && !matchesString(doc["session_id"], sessionId_))
              || (object.containsKey("event_id") && !matchesString(doc["event_id"], pendingEventId_))
              || (object.containsKey("schema_version") && (!doc["schema_version"].is<int>()
                  || doc["schema_version"].as<int>() != schemaVersion_))) return fail(Error::Protocol);
          const std::string code = doc["error"]["code"].as<std::string>();
          if (code == "event_processing" || code == "speech_processing") return recover(Error::Transport);
          if (code == "session_not_started" || code == "session_closed") return fail(Error::Protocol, true);
          return fail(Error::Protocol);
        }
      }
      return fail(Error::Transport);
    }
    EventJsonDocument doc(4096);
    if (deserializeJson(doc, response) || doc.overflowed()
        || !doc["ok"].is<bool>() || !doc["ok"].as<bool>()
        || !doc["schema_version"].is<int>() || doc["schema_version"].as<int>() != schemaVersion_
        || !matchesString(doc["session_id"], sessionId_)
        || !matchesString(doc["event_id"], pendingEventId_)) return fail(Error::Protocol);
    if (!doc["stackchan_action"].isNull()) return fail(Error::UnsupportedAction);
    const auto object = doc.as<ArduinoJson::JsonObjectConst>();
    if (schemaVersion_ == 2 && (!object.containsKey("device_action")
        || !object.containsKey("reply"))) return fail(Error::Protocol);
    const std::string state = doc["state"] | "";
    const std::string action = doc["action"] | "";
    const bool stopEvent = pendingType_ == "stop";
    const bool oneShotCompletion = oneShot_ && pendingType_ == "action_completed";
    if (stopEvent || oneShotCompletion) {
      if (state != "closed" || action != "stopped" || !doc["reply"].isNull()
          || !doc["device_action"].isNull()) return fail(Error::Protocol);
      abandon();
      return Outcome::Stopped;
    }
    if (state != "active" && state != "quiet") return fail(Error::Protocol);
    const bool speechEvent = pendingType_ == "speech";
    if (speechEvent && action == "reply") {
      if (!doc["device_action"].isNull()
          || !doc["reply"]["id"].is<const char*>()
          || !doc["reply"]["text"].is<const char*>()) return fail(Error::Protocol);
      const std::string replyId = doc["reply"]["id"].as<std::string>();
      const std::string replyText = doc["reply"]["text"].as<std::string>();
      if (!validId(replyId) || replyText.empty()
          || replyText.find('\0') != std::string::npos) return fail(Error::Protocol);
      if (replyText.find("[[VISION_") != std::string::npos) return fail(Error::UnsupportedAction);
      clearResult();
      replyId_ = replyId;
      replyText_ = replyText;
      quiet_ = state == "quiet";
      return accept(Phase::PlaybackPending, Outcome::Reply);
    }
    if (schemaVersion_ == 2 && speechEvent && action == "perform") {
      DeviceAction deviceAction;
      if (state != "active" || !doc["reply"].isNull()) return fail(Error::Protocol);
      if (!parseDeviceAction(doc["device_action"], deviceAction)) return fail(Error::UnsupportedAction);
      clearResult();
      deviceAction_ = deviceAction;
      quiet_ = false;
      return accept(Phase::ActionPending, Outcome::Perform);
    }
    if (!doc["reply"].isNull() || !doc["device_action"].isNull()
        || (action != "listen" && !(speechEvent && action == "wait"))
        || (!speechEvent && state != "active")) return fail(Error::Protocol);
    clearResult();
    quiet_ = state == "quiet";
    return accept(Phase::Listening, action == "wait" ? Outcome::Wait : Outcome::Listen);
  }

  Outcome accept(Phase phase, Outcome outcome) {
    phase_ = phase;
    error_ = Error::None;
    needsRestart_ = false;
    clearPending();
    return outcome;
  }

  ConversationTransport& transport_;
  IdFactory makeId_;
  const int schemaVersion_;
  Phase phase_ = Phase::Off;
  Error error_ = Error::None;
  bool quiet_ = false;
  bool oneShot_ = false;
  bool needsRestart_ = false;
  std::string sessionId_;
  std::string replyId_;
  std::string replyText_;
  DeviceAction deviceAction_;
  std::string pendingBody_;
  std::string pendingEventId_;
  std::string pendingType_;
};
}  // namespace stackchan

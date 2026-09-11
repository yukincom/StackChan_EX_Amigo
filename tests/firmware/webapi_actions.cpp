#include "prelude.h"
#include <cstdio>
#include <functional>
#include <stdexcept>
#include <vector>

static uint32_t nowMs = 100;
uint32_t millis() { return nowMs; }
void delay(uint32_t duration) { nowMs += duration; }
SerialStub Serial;
M5Stub M5;
m5avatar::Avatar avatar;
Robot fixtureRobot;
Robot* robot = &fixtureRobot;
bool isSubWindowON = false;
volatile bool g_cameraBusy = false;
uint8_t m5spk_virtual_channel = 0;
static int audioCalls = 0, cameraCalls = 0, errorCalls = 0;
static bool audioResult = true, cameraResult = true;
static bool audioChecked = false, audioTalking = true;
static m5avatar::Expression expressionAtAudio = m5avatar::Expression::Neutral;
static std::string cameraSession, cameraAction;
static std::string errorEndpoint;
void setActivityExpression(m5avatar::Expression value) { avatar.setExpression(value); }
void setExplicitExpression(m5avatar::Expression value) { avatar.setExpression(value); }
void setIdleExpression() { avatar.setExpression(m5avatar::Expression::Neutral); }
void finishExpressionActivity(m5avatar::Expression value) { if (avatar.getExpression() == value) setIdleExpression(); }
void refreshIdleExpression(bool) {}
bool playMP3SD(const char*, bool talking, bool checked) {
  ++audioCalls; audioChecked = checked; audioTalking = talking;
  expressionAtAudio = avatar.getExpression(); return audioResult;
}
bool playMP3URL(const char*, bool checked, bool talking) {
  ++audioCalls; audioChecked = checked; audioTalking = talking;
  expressionAtAudio = avatar.getExpression(); return audioResult;
}
bool playLocalPrompt(const char*, bool) { return false; }
void playInputErrorPrompt(bool, const char* endpoint) {
  ++errorCalls; errorEndpoint = endpoint ? endpoint : "";
}
bool camera_capture_post_upload(const String&, const char*, const char*, const char*, const char*, const char*, const char* session, const char* action) {
  ++cameraCalls; cameraSession = session; cameraAction = action; return cameraResult;
}
static unsigned nextId = 0;
static std::string makeId() { return "fixture-" + std::to_string(++nextId); }
struct Transport final : stackchan::ConversationTransport {
  std::vector<std::string> requests;
  int failures = 0;
  int completionStatus = 200;
  bool oneShot = false;
  std::string actionType = "audio_playback";
  int post(const std::string& body, std::string& response) override {
    requests.push_back(body);
    ArduinoJson::DynamicJsonDocument req(2048), res(4096);
    deserializeJson(req, body);
    if (std::string(req["type"] | "") == "action_completed" && failures > 0) { --failures; return -1; }
    if (std::string(req["type"] | "") == "action_completed" && completionStatus != 200) {
      response = "{\"error\":{\"code\":\"action_id_mismatch\"}}";
      return completionStatus;
    }
    res["ok"] = true;
    res["schema_version"] = 2;
    res["session_id"] = req["session_id"];
    res["event_id"] = req["event_id"];
    res["reply"] = nullptr;
    res["device_action"] = nullptr;
    const std::string type = req["type"] | "";
    res["state"] = "active";
    res["action"] = "listen";
    if (type == "stop" || (oneShot && type == "action_completed")) { res["state"] = "closed"; res["action"] = "stopped"; }
    if (type == "speech") {
      res["action"] = "perform";
      addAction(res["device_action"].to<ArduinoJson::JsonObject>(), actionType);
    }
    serializeJson(res, response);
    return 200;
  }
  static void addAction(ArduinoJson::JsonObject action, const std::string& type) {
    action["action_id"] = "action-A";
    action["type"] = type;
    if (type == "audio_playback") action["audio_url"] = "http://fixture.invalid/song.mp3";
    else {
      action["camera_mode"] = "transient";
      action["camera_requester"] = "user";
      action["transcript"] = "fixture";
      action["speaker"] = "master";
      action["speaker_label"] = "";
    }
  }
};
static Transport alwaysTransport, touchTransport;
namespace stackchan {
ConversationSession& conversationSession() { static ConversationSession session(alwaysTransport, makeId, 2); return session; }
ConversationSession& touchActionSession() { static ConversationSession session(touchTransport, makeId, 2); return session; }
std::string newConversationId() { return makeId(); }
}
// The actual product queue/admission/completion implementation under test.
#include "WebAPI.cpp"

static void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
static void reset() {
  cancel_conversation_action();
  stackchan::conversationSession().abandon();
  stackchan::touchActionSession().abandon();
  alwaysTransport = Transport{}; touchTransport = Transport{};
  s_action_session = nullptr; s_action_finished = false;
  s_play_request_pending = false; s_play_active = false; s_play_local = false;
  s_camera_trigger_pending = false; s_camera_wait_expired = false;
  clear_camera_trigger_awaiting();
  audioCalls = cameraCalls = errorCalls = 0;
  errorEndpoint.clear();
  cameraResult = audioResult = true;
  audioChecked = false; audioTalking = true;
  fixtureRobot.chatCalls = fixtureRobot.speechCalls = 0;
  setIdleExpression();
  nowMs += 100000;
}
static stackchan::ConversationSession& prepareAlways(const char* type) {
  auto& session = stackchan::conversationSession();
  alwaysTransport.actionType = type;
  require(session.start(), "start failed");
  require(session.speech("fixture") == stackchan::ConversationSession::Outcome::Perform, "perform failed");
  return session;
}
static stackchan::ConversationSession& prepareTouch(const char* type = "audio_playback") {
  auto& session = stackchan::touchActionSession();
  touchTransport.oneShot = true;
  ArduinoJson::DynamicJsonDocument envelope(2048);
  envelope["action_schema_version"] = 2;
  envelope["session_id"] = "touch-session";
  envelope["event_id"] = "touch-event";
  envelope["action"] = "perform";
  Transport::addAction(envelope["device_action"].to<ArduinoJson::JsonObject>(), type);
  require(session.adoptTouchAction("touch-session", "touch-event", envelope.as<ArduinoJson::JsonVariantConst>()), "adopt failed");
  return session;
}
static void push(const std::string& session, const char* action = "action-A", const char* status = "succeeded") {
  ArduinoJson::DynamicJsonDocument doc(2048);
  doc["voice_url"] = "http://fixture.invalid/vision.mp3";
  doc["session_id"] = session;
  doc["action_id"] = action;
  doc["action_status"] = status;
  server.requestBody.clear(); serializeJson(doc, server.requestBody);
  server.responseCode = 0;
  handle_play();
}
static void testSongAckLost() {
  auto& session = prepareAlways("audio_playback");
  require(request_conversation_action(session), "song queue failed");
  alwaysTransport.failures = 1;
  process_play_request();
  require(audioCalls == 1 && session.pendingRetry(), "song missing retry");
  const auto firstAck = alwaysTransport.requests.back();
  nowMs += 1001; process_play_request();
  require(audioCalls == 1 && cameraCalls == 0, "song replayed on retry");
  require(alwaysTransport.requests.back() == firstAck, "ack changed on retry");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening && !s_action_session, "always not restored");
}
static void testCameraCorrelationAckLost() {
  auto& session = prepareAlways("camera_capture");
  const std::string sid = session.sessionId();
  require(request_conversation_action(session), "camera queue failed");
  process_camera_trigger_request();
  require(cameraCalls == 1 && cameraSession == sid && cameraAction == "action-A", "capture correlation lost");
  push(sid, "old-action"); require(server.responseCode == 409 && !s_play_request_pending, "old action accepted");
  server.requestBody = "{\"voice_url\":\"http://fixture.invalid/old.mp3\"}";
  handle_play(); require(server.responseCode == 409 && !s_play_request_pending, "uncorrelated push accepted");
  push(sid); require(server.responseCode == 200 && s_play_request_pending, "correlated push rejected");
  push(sid); require(server.responseCode == 200 && s_play_request_pending, "identical queued duplicate rejected");
  alwaysTransport.failures = 1;
  process_play_request(); require(audioCalls == 1 && session.pendingRetry(), "camera reply missing retry");
  const auto ack = alwaysTransport.requests.back();
  nowMs += 1001; process_play_request();
  require(audioCalls == 1 && cameraCalls == 1 && alwaysTransport.requests.back() == ack, "action repeated on ack retry");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening, "camera not restored");
  push(sid); require(server.responseCode == 409 && !s_play_request_pending, "completed old push admitted");
}
static void testTimeoutOnce() {
  auto& session = prepareAlways("camera_capture");
  const auto sid = session.sessionId();
  request_conversation_action(session); process_camera_trigger_request();
  nowMs += 8001;
  is_camera_trigger_active(); is_camera_trigger_active();
  require(errorCalls == 0, "state query emitted failure audio");
  push(sid); require(server.responseCode == 409 && !s_play_request_pending, "expired push accepted");
  process_camera_trigger_request(); process_camera_trigger_request();
  require(errorCalls == 1 && cameraCalls == 1 && audioCalls == 0, "timeout feedback repeated or action replayed");
  require(errorEndpoint == "vision_error", "camera timeout lost dedicated SD phrase");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening && !is_camera_trigger_active(), "timeout failed to restore listening");
  require(alwaysTransport.requests.back().find("\"status\":\"failed\"") != std::string::npos, "timeout acknowledged success");
}
static void testTouchOrphanRetry() {
  auto& session = prepareTouch();
  s_play_request_pending = true; // A pre-existing physical queue denies new admission.
  require(!request_conversation_action(session), "busy queue accepted touch");
  s_play_request_pending = false;
  touchTransport.failures = 1;
  require(!session.actionCompleted(false) && session.pendingRetry(), "touch ack loss not retained");
  const auto ack = touchTransport.requests.back();
  nowMs += 1001; process_play_request();
  require(session.phase() == stackchan::ConversationSession::Phase::Off && !session.pendingRetry(), "touch orphan not recovered");
  require(touchTransport.requests.back() == ack && audioCalls == 0 && cameraCalls == 0, "touch orphan replayed or mutated ack");
}
static void testSongDownloadFailure() {
  auto& session = prepareAlways("audio_playback");
  audioResult = false;
  request_conversation_action(session); process_play_request();
  require(audioCalls == 1 && errorCalls == 1 && !is_camera_trigger_active(), "download failure cleanup missing");
  require(errorEndpoint.empty(), "song failure used image error phrase");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening, "download failure changed input session");
  require(alwaysTransport.requests.back().find("\"status\":\"failed\"") != std::string::npos, "download failure acknowledged success");
}
static void testBusyLegacyGuards() {
  auto& session = prepareAlways("camera_capture");
  request_conversation_action(session); process_camera_trigger_request();
  handle_chat(); require(server.responseCode == 409 && fixtureRobot.chatCalls == 0, "legacy chat escaped action gate");
  handle_speech(); require(server.responseCode == 409 && fixtureRobot.speechCalls == 0, "legacy speech escaped action gate");
  require(s_camera_trigger_awaiting, "legacy handler cleared camera wait");
  nowMs += 8001; process_camera_trigger_request();
  handle_chat(); require(server.responseCode == 200 && fixtureRobot.chatCalls == 1, "legacy chat remained disabled after cleanup");
  handle_speech(); require(server.responseCode == 200 && fixtureRobot.speechCalls == 1, "legacy speech remained disabled after cleanup");
}
static void testFailedPushSingleExplanation() {
  auto& session = prepareAlways("camera_capture");
  const auto sid = session.sessionId();
  request_conversation_action(session); process_camera_trigger_request();
  push(sid, "action-A", "failed"); require(server.responseCode == 200, "failed result push rejected");
  alwaysTransport.failures = 1;
  process_play_request();
  require(audioCalls == 0 && errorCalls == 1 && errorEndpoint == "vision_error", "image failure did not use SD-only explanation");
  const auto ack = alwaysTransport.requests.back();
  nowMs += 1001; process_play_request();
  require(audioCalls == 0 && errorCalls == 1 && alwaysTransport.requests.back() == ack, "image failure replayed on ACK retry");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening, "failed push did not restore listening");
  require(alwaysTransport.requests.back().find("\"status\":\"failed\"") != std::string::npos, "failed action acknowledged success");
}
static void testFailedLocalPushDedicatedPrompt() {
  auto& session = prepareAlways("camera_capture");
  const auto sid = session.sessionId();
  request_conversation_action(session); process_camera_trigger_request();
  ArduinoJson::DynamicJsonDocument doc(2048);
  doc["session_id"] = sid; doc["action_id"] = "action-A"; doc["action_status"] = "failed";
  doc["endpoint"] = "../error";
  server.requestBody.clear(); serializeJson(doc, server.requestBody); handle_play_local();
  require(server.responseCode == 400 && s_camera_trigger_awaiting, "invalid cache endpoint touched queue/wait");
  doc["endpoint"] = "error";
  server.requestBody.clear(); serializeJson(doc, server.requestBody); handle_play_local();
  require(server.responseCode == 200, "valid failed cache push rejected");
  process_play_request();
  require(audioCalls == 0 && errorCalls == 1 && errorEndpoint == "vision_error" && !is_camera_trigger_active(), "failed local push did not choose dedicated SD phrase");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening, "failed local push stranded session");
}
static void testSuccessfulImageAudioFailure() {
  auto& session = prepareAlways("camera_capture");
  request_conversation_action(session); process_camera_trigger_request();
  push(session.sessionId());
  audioResult = false;
  process_play_request();
  require(audioCalls == 1 && errorCalls == 1 && errorEndpoint.empty(), "successful image audio failure used image failure phrase");
  require(session.phase() == stackchan::ConversationSession::Phase::Listening, "image audio failure stranded input");
}
static void testTouchCameraFailure() {
  auto& session = prepareTouch("camera_capture");
  request_conversation_action(session); process_camera_trigger_request();
  push(session.sessionId(), "action-A", "failed");
  process_play_request();
  require(audioCalls == 0 && errorCalls == 1 && errorEndpoint == "vision_error", "Touch camera failure lost SD phrase");
  require(session.phase() == stackchan::ConversationSession::Phase::Off && !is_camera_trigger_active(), "Touch camera failure did not restore touch wait");
}
static void testTouchSuccessfulOneShot() {
  auto& session = prepareTouch();
  require(request_conversation_action(session), "touch action queue rejected");
  process_play_request();
  require(audioCalls == 1 && session.phase() == stackchan::ConversationSession::Phase::Off, "touch completion not closed");
  require(!s_action_session && !session.pendingRetry() && !is_camera_trigger_active(), "touch mode gate not released");
  prepareTouch(); // The actual session accepts the next explicit Touch action.
}
static void testFatalCompletionReleasesOwner() {
  auto& session = prepareAlways("audio_playback");
  request_conversation_action(session);
  alwaysTransport.completionStatus = 409;
  process_play_request();
  nowMs += 1001; process_play_request();
  require(!conversation_action_owns(session), "fatal non-retryable ACK retains action owner forever");
  require(session.phase() == stackchan::ConversationSession::Phase::Off, "fatal ACK did not close old session");
  require(alwaysTransport.requests.back().find("\"type\":\"stop\"") != std::string::npos, "fatal ACK did not send stop");
  const auto sent = alwaysTransport.requests.size();
  process_play_request();
  require(alwaysTransport.requests.size() == sent && audioCalls == 1, "fatal cleanup replayed stop/audio");
}
static void testCancelOrphanTouch() {
  auto& session = prepareTouch();
  touchTransport.failures = 1;
  require(!session.actionCompleted(false) && session.pendingRetry(), "orphan cancellation setup failed");
  cancel_conversation_action();
  require(session.phase() == stackchan::ConversationSession::Phase::Off && !session.pendingRetry(), "explicit cancel retained orphan touch ACK");
  const auto requestCount = touchTransport.requests.size();
  nowMs += 1001; process_play_request();
  require(touchTransport.requests.size() == requestCount && audioCalls == 0 && cameraCalls == 0, "explicit cancel scheduled retry or action");
}
static void testCaptureFailure() {
  auto& session = prepareAlways("camera_capture");
  cameraResult = false;
  request_conversation_action(session); process_camera_trigger_request();
  require(cameraCalls == 1 && audioCalls == 0 && errorCalls == 1, "capture failure repeated or lost feedback");
  require(errorEndpoint == "vision_error", "capture failure lost dedicated SD phrase");
  require(!is_camera_trigger_active() && session.phase() == stackchan::ConversationSession::Phase::Listening, "capture failure stranded input");
  require(alwaysTransport.requests.back().find("\"status\":\"failed\"") != std::string::npos, "capture failure acknowledged success");
}
static void testCancelRejectsLatePush() {
  auto& session = prepareAlways("camera_capture");
  const auto sid = session.sessionId();
  request_conversation_action(session); process_camera_trigger_request();
  cancel_conversation_action();
  push(sid);
  require(server.responseCode == 409 && !s_play_request_pending, "cancelled action admitted delayed push");
  nowMs += 8001; process_camera_trigger_request(); process_play_request();
  require(session.phase() == stackchan::ConversationSession::Phase::Off && !is_camera_trigger_active(), "explicit stop reopened input");
  require(audioCalls == 0 && cameraCalls == 1 && errorCalls == 0, "cancel left timeout feedback or replay");
}
static void testRejectedPushDoesNotAlterQueue() {
  auto& session = prepareAlways("camera_capture");
  const auto sid = session.sessionId();
  request_conversation_action(session); process_camera_trigger_request();
  push("other-session"); require(server.responseCode == 409 && s_camera_trigger_awaiting, "wrong session changed wait");
  push(sid, "action-A", "invalid"); require(server.responseCode == 400 && s_camera_trigger_awaiting, "invalid status changed wait");
  push(sid); require(server.responseCode == 200, "first valid push rejected");
  push(sid, "action-A", "failed"); require(server.responseCode == 409, "changed duplicate status accepted");
  process_play_request();
  require(audioCalls == 1 && errorCalls == 0, "rejected duplicate changed original playback");
  require(alwaysTransport.requests.back().find("\"status\":\"succeeded\"") != std::string::npos, "rejected duplicate changed completion");
}
int main() {
  const std::vector<std::pair<const char*, std::function<void()>>> tests = {
    {"song_ack_lost_no_replay", testSongAckLost},
    {"camera_correlation_ack_lost_no_replay", testCameraCorrelationAckLost},
    {"camera_timeout_once_main_only", testTimeoutOnce},
    {"touch_enqueue_denied_ack_recovery", testTouchOrphanRetry},
    {"song_download_failure_cleanup", testSongDownloadFailure},
    {"busy_legacy_chat_speech_guards", testBusyLegacyGuards},
    {"failed_push_single_confusion_explanation", testFailedPushSingleExplanation},
    {"failed_local_push_dedicated_prompt", testFailedLocalPushDedicatedPrompt},
    {"successful_image_audio_failure", testSuccessfulImageAudioFailure},
    {"touch_camera_failure", testTouchCameraFailure},
    {"touch_successful_one_shot_closed", testTouchSuccessfulOneShot},
    {"fatal_completion_releases_owner", testFatalCompletionReleasesOwner},
    {"explicit_cancel_orphan_touch_ack", testCancelOrphanTouch},
    {"capture_failure_releases_input", testCaptureFailure},
    {"explicit_cancel_rejects_late_push", testCancelRejectsLatePush},
    {"rejected_push_preserves_original_queue", testRejectedPushDoesNotAlterQueue},
  };
  unsigned failures = 0;
  for (const auto& test : tests) {
    reset();
    try { test.second(); std::printf("PASS %s\n", test.first); }
    catch (const std::exception& e) { ++failures; std::printf("FAIL %s: %s\n", test.first, e.what()); }
  }
  std::printf("%zu tests, %u failures\n", tests.size(), failures);
  return failures ? 1 : 0;
}

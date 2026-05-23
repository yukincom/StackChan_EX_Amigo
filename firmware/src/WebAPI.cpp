#include <ESP32WebServer.h>
#include <ArduinoJson.h>
#include <nvs.h>
#include "WebAPI.h"
#include "Avatar.h"
#include "llm/ChatGPT/ChatGPT.h"
#include "llm/ChatGPT/FunctionCall.h"
#include "Robot.h"
#include "driver/PlayMP3.h"
#if defined(ENABLE_CAMERA)
#include "driver/Camera.h"
#endif

using namespace m5avatar;
extern Avatar avatar;
extern uint8_t m5spk_virtual_channel;
extern volatile bool g_cameraBusy;

ESP32WebServer server(80);
static bool s_play_request_pending = false;
static bool s_play_active = false;
static bool s_play_local = false;
static String s_play_voice_url = "";
static String s_play_local_endpoint = "";
static bool s_camera_trigger_pending = false;
static bool s_camera_trigger_awaiting = false;
static uint32_t s_camera_trigger_await_deadline = 0;
static String s_camera_trigger_base_url = "";
static String s_camera_trigger_requester = "user";
static String s_camera_trigger_context = "";
static String s_camera_trigger_mode = "transient";
static String s_camera_trigger_speaker = "master";
static String s_camera_trigger_speaker_label = "";
static String s_camera_trigger_announce_endpoint = "";

static bool is_camera_trigger_awaiting_now() {
  if (!s_camera_trigger_awaiting) {
    return false;
  }
  if ((int32_t)(millis() - s_camera_trigger_await_deadline) >= 0) {
    s_camera_trigger_awaiting = false;
    s_camera_trigger_await_deadline = 0;
    return false;
  }
  return true;
}

void mark_camera_trigger_awaiting(uint32_t timeout_ms) {
  s_camera_trigger_awaiting = true;
  s_camera_trigger_await_deadline = millis() + timeout_ms;
}

void clear_camera_trigger_awaiting(void) {
  s_camera_trigger_awaiting = false;
  s_camera_trigger_await_deadline = 0;
}

bool is_camera_trigger_active(void) {
  return g_cameraBusy || s_camera_trigger_pending || is_camera_trigger_awaiting_now() || s_play_request_pending || s_play_active;
}

static String resolve_camera_trigger_base_url()
{
  String base_url = robot->m_config.getExConfig().llm.base_url;
  if (base_url == "") {
    base_url = robot->m_config.getExConfig().stt.base_url;
  }
  if (base_url == "") {
    base_url = robot->m_config.getExConfig().tts.base_url;
  }
  return base_url;
}

static bool enqueue_camera_trigger_internal(
  const String& base_url,
  const String& requester,
  const String& context,
  const String& mode,
  const String& speaker,
  const String& speaker_label,
  const String& announce_endpoint
)
{
  if (base_url == "") {
    return false;
  }
  if (s_camera_trigger_pending || g_cameraBusy) {
    return false;
  }

  clear_camera_trigger_awaiting();
  s_camera_trigger_base_url = base_url;
  s_camera_trigger_requester = requester == "" ? "user" : requester;
  s_camera_trigger_context = context;
  s_camera_trigger_mode = mode == "archive" ? "archive" : "transient";
  s_camera_trigger_speaker = speaker == "" ? "master" : speaker;
  s_camera_trigger_speaker_label = speaker_label;
  s_camera_trigger_announce_endpoint = announce_endpoint;
  s_camera_trigger_pending = true;
  return true;
}

bool request_camera_trigger(
  const String& requester,
  const String& context,
  const String& mode,
  const String& speaker,
  const String& speaker_label,
  const String& announce_endpoint
)
{
  String base_url = resolve_camera_trigger_base_url();
  return enqueue_camera_trigger_internal(base_url, requester, context, mode, speaker, speaker_label, announce_endpoint);
}

static bool is_safe_local_audio_endpoint(const String& endpoint) {
  if (endpoint.length() == 0) {
    return false;
  }
  if (endpoint.indexOf('/') >= 0 || endpoint.indexOf('\\') >= 0) {
    return false;
  }
  if (endpoint.indexOf("..") >= 0) {
    return false;
  }
  return true;
}

// C++11 multiline string constants are neato...
static const char HEAD[] PROGMEM = R"KEWL(
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>AIｽﾀｯｸﾁｬﾝ</title>
</head>)KEWL";

#define IMPORT_FILE(section, filename, symbol) \
static constexpr const char* filename_##symbol = filename; \
extern const uint8_t symbol[], sizeof_##symbol[]; \
asm(\
  ".section " #section "\n"\
  ".balign 4\n"\
  ".global " #symbol "\n"\
  #symbol ":\n"\
  ".incbin \"incbin/" filename "\"\n"\
  ".global sizeof_" #symbol "\n"\
  ".set sizeof_" #symbol ", . - " #symbol "\n"\
  ".balign 4\n"\
  ".section \".text\"\n")

void handleRoot() {
  String html = String(HEAD)
    + String("<body><p>Firmware personalize UI is removed in this build. Use the local server admin UI.</p></body></html>");
  server.send(200, "text/html", html);
}

void handleNotFound(){
  String message = "File Not Found\n\n";
  message += "URI: ";
  message += server.uri();
  message += "\nMethod: ";
  message += (server.method() == HTTP_GET)?"GET":"POST";
  message += "\nArguments: ";
  message += server.args();
  message += "\n";
  for (uint8_t i=0; i<server.args(); i++){
    message += " " + server.argName(i) + ": " + server.arg(i) + "\n";
  }
//  server.send(404, "text/plain", message);
  server.send(404, "text/html", String(HEAD) + String("<body>") + message + String("</body>"));
}

void handle_speech() {
  String message = server.arg("say");
  String speaker = server.arg("voice");
  //if(speaker != "") {
  //  TTS_PARMS = TTS_SPEAKER + speaker;
  //}
  Serial.println(message);
  ////////////////////////////////////////
  // 音声の発声
  ////////////////////////////////////////
  //avatar.setExpression(Expression::Happy);
  robot->speech(message);
  server.send(200, "text/plain", String("OK"));
}

void handle_chat() {
  static String response = "";
  // tts_parms_no = 1;
  String text = server.arg("text");
  String speaker = server.arg("voice");
  //if(speaker != "") {
  //  TTS_PARMS = TTS_SPEAKER + speaker;
  //}

  robot->chat(text);

  server.send(200, "text/html", String(HEAD)+String("<body>")+response+String("</body>"));
}

void handle_face() {
  String expression = server.arg("expression");
  expression = expression + "\n";
  Serial.println(expression);
  switch (expression.toInt())
  {
    case 0: avatar.setExpression(Expression::Neutral); break;
    case 1: avatar.setExpression(Expression::Happy); break;
    case 2: avatar.setExpression(Expression::Sleepy); break;
    case 3: avatar.setExpression(Expression::Doubt); break;
    case 4: avatar.setExpression(Expression::Sad); break;
    case 5: avatar.setExpression(Expression::Angry); break;  
  } 
  server.send(200, "text/plain", String("OK"));
}

void handle_play() {
  if (server.method() != HTTP_POST) {
    server.send(405, "application/json", "{\"success\":false,\"error\":\"method_not_allowed\"}");
    return;
  }

  DynamicJsonDocument doc(512);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error) {
    server.send(400, "application/json", "{\"success\":false,\"error\":\"invalid_json\"}");
    return;
  }

  String voice_url = doc["voice_url"].as<String>();
  if (voice_url == "") {
    server.send(400, "application/json", "{\"success\":false,\"error\":\"voice_url_required\"}");
    return;
  }

  clear_camera_trigger_awaiting();

  if (s_play_request_pending || s_play_active) {
    server.send(409, "application/json", "{\"success\":false,\"error\":\"play_busy\"}");
    return;
  }

  s_play_voice_url = voice_url;
  s_play_local_endpoint = "";
  s_play_local = false;
  s_play_request_pending = true;
  server.send(200, "application/json", "{\"success\":true}");
}

void handle_play_local() {
  if (server.method() != HTTP_POST) {
    server.send(405, "application/json", "{\"success\":false,\"error\":\"method_not_allowed\"}");
    return;
  }

  DynamicJsonDocument doc(512);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error) {
    server.send(400, "application/json", "{\"success\":false,\"error\":\"invalid_json\"}");
    return;
  }

  String endpoint = doc["endpoint"].as<String>();
  if (!is_safe_local_audio_endpoint(endpoint)) {
    server.send(400, "application/json", "{\"success\":false,\"error\":\"endpoint_required\"}");
    return;
  }

  clear_camera_trigger_awaiting();

  if (s_play_request_pending || s_play_active) {
    server.send(409, "application/json", "{\"success\":false,\"error\":\"play_busy\"}");
    return;
  }

  s_play_voice_url = "";
  s_play_local_endpoint = endpoint;
  s_play_local = true;
  s_play_request_pending = true;
  server.send(200, "application/json", "{\"success\":true}");
}

void handle_audio_status() {
  if (g_cameraBusy) {
    server.send(200, "application/json", "{\"ready\":false,\"mode\":\"camera\"}");
    return;
  }
  if (is_camera_trigger_awaiting_now()) {
    server.send(200, "application/json", "{\"ready\":false,\"mode\":\"camera_await\"}");
    return;
  }
  if (s_play_active) {
    server.send(200, "application/json", "{\"ready\":false,\"mode\":\"play\"}");
    return;
  }
  if (s_play_request_pending) {
    server.send(200, "application/json", "{\"ready\":false,\"mode\":\"play_queue\"}");
    return;
  }
  server.send(200, "application/json", "{\"ready\":true,\"mode\":\"idle\"}");
}

void process_play_request(void)
{
  if (!s_play_request_pending || g_cameraBusy) {
    return;
  }

  String voice_url = s_play_voice_url;
  String local_endpoint = s_play_local_endpoint;
  bool local_play = s_play_local;
  s_play_request_pending = false;
  s_play_voice_url = "";
  s_play_local_endpoint = "";
  s_play_local = false;
  s_play_active = true;

  bool result = false;
  if (local_play) {
    String sd_path = "/stack_sd_audio/" + local_endpoint + ".mp3";
    Serial.printf("[PLAY-LOCAL] %s\n", sd_path.c_str());
    result = playMP3SD(sd_path.c_str());
    Serial.printf("[PLAY-LOCAL] result=%s\n", result ? "ok" : "ng");
  } else {
    Serial.printf("[PLAY] %s\n", voice_url.c_str());
    result = playMP3URL(voice_url.c_str());
    Serial.printf("[PLAY] result=%s\n", result ? "ok" : "ng");
  }
  s_play_active = false;
}

void handle_camera_trigger() {
#if !defined(ENABLE_CAMERA)
  server.send(503, "application/json", "{\"success\":false,\"error\":\"camera_disabled\"}");
#else
  if (server.method() != HTTP_POST) {
    server.send(405, "application/json", "{\"success\":false,\"error\":\"method_not_allowed\"}");
    return;
  }

  DynamicJsonDocument doc(512);
  DeserializationError error = deserializeJson(doc, server.arg("plain"));
  if (error) {
    server.send(400, "application/json", "{\"success\":false,\"error\":\"invalid_json\"}");
    return;
  }

  if (s_camera_trigger_pending || g_cameraBusy) {
    server.send(409, "application/json", "{\"success\":false,\"error\":\"camera_busy\"}");
    return;
  }

  String requester = doc["requester"].as<String>();
  if (requester == "") {
    requester = "user";
  }
  String context = doc["context"].as<String>();
  String mode = doc["mode"].as<String>();
  if (mode != "archive") {
    mode = "transient";
  }
  String speaker = doc["speaker"].as<String>();
  if (speaker == "") {
    speaker = "master";
  }
  String speaker_label = doc["speaker_label"].as<String>();
  String announce_endpoint = doc["announce_endpoint"].as<String>();

  String base_url = resolve_camera_trigger_base_url();
  if (base_url == "") {
    server.send(500, "application/json", "{\"success\":false,\"error\":\"base_url_required\"}");
    return;
  }

  if (!enqueue_camera_trigger_internal(base_url, requester, context, mode, speaker, speaker_label, announce_endpoint)) {
    server.send(409, "application/json", "{\"success\":false,\"error\":\"camera_busy\"}");
    return;
  }

  server.send(200, "application/json", "{\"success\":true}");
#endif
}

void process_camera_trigger_request(void)
{
#if defined(ENABLE_CAMERA)
  if (!s_camera_trigger_pending || g_cameraBusy) {
    return;
  }

  String base_url = s_camera_trigger_base_url;
  String requester = s_camera_trigger_requester;
  String context = s_camera_trigger_context;
  String mode = s_camera_trigger_mode;
  String speaker = s_camera_trigger_speaker;
  String speaker_label = s_camera_trigger_speaker_label;
  String announce_endpoint = s_camera_trigger_announce_endpoint;
  s_camera_trigger_pending = false;
  s_camera_trigger_speaker_label = "";
  s_camera_trigger_announce_endpoint = "";
  clear_camera_trigger_awaiting();

  Serial.printf(
    "[CAMERA_TRIGGER] requester=%s speaker=%s speaker_label=%s mode=%s context=%s\n",
    requester.c_str(),
    speaker.c_str(),
    speaker_label.c_str(),
    mode.c_str(),
    context.c_str()
  );
  g_cameraBusy = true;
  avatar.set_isSubWindowEnable(false);
  if (announce_endpoint != "") {
    playLocalPrompt(announce_endpoint.c_str());
  }
  avatar.setExpression(Expression::Doubt);
  bool result = camera_capture_post_upload(
    base_url,
    requester.c_str(),
    context.c_str(),
    mode.c_str(),
    speaker.c_str(),
    speaker_label.c_str()
  );
  avatar.set_isSubWindowEnable(isSubWindowON);
  g_cameraBusy = false;
  if (result) {
    mark_camera_trigger_awaiting();
  } else {
    avatar.setExpression(Expression::Neutral);
    clear_camera_trigger_awaiting();
  }
  Serial.printf("[CAMERA_TRIGGER] result=%s\n", result ? "ok" : "ng");
#endif
}

#if 0
void handle_setting() {
  String value = server.arg("volume");
  String led = server.arg("led");
  String speaker = server.arg("speaker");
//  volume = volume + "\n";
  Serial.println(speaker);
  Serial.println(value);
  size_t speaker_no;

  if(speaker != ""){
    speaker_no = speaker.toInt();
    if(speaker_no > 60) {
      speaker_no = 60;
    }
    TTS_SPEAKER_NO = String(speaker_no);
    TTS_PARMS = TTS_SPEAKER + TTS_SPEAKER_NO;
  }

  if(value == "") value = "180";
  size_t volume = value.toInt();
  uint8_t led_onoff = 0;
  uint32_t nvs_handle;
  if (ESP_OK == nvs_open("setting", NVS_READWRITE, &nvs_handle)) {
    if(volume > 255) volume = 255;
    nvs_set_u32(nvs_handle, "volume", volume);
    if(led != "") {
      if(led == "on") led_onoff = 1;
      else  led_onoff = 0;
      nvs_set_u8(nvs_handle, "led", led_onoff);
    }
    nvs_set_u8(nvs_handle, "speaker", speaker_no);

    nvs_close(nvs_handle);
  }
  M5.Speaker.setVolume(volume);
  M5.Speaker.setChannelVolume(m5spk_virtual_channel, volume);
  server.send(200, "text/plain", String("OK"));
}
#endif


void init_web_server(void)
{
  // Files
  //
  server.on("/", handleRoot);


  // APIs
  //
  server.on("/speech", handle_speech);
  server.on("/face", handle_face);
  server.on("/chat", handle_chat);
  server.on("/play", HTTP_POST, handle_play);
  server.on("/play_local", HTTP_POST, handle_play_local);
  server.on("/audio/status", handle_audio_status);
  server.on("/camera/trigger", HTTP_POST, handle_camera_trigger);

  // Other
  //
  server.onNotFound(handleNotFound);
  server.on("/inline", [](){
    server.send(200, "text/plain", "this works as well");
  });

  server.begin();
  Serial.println("HTTP server started");
  M5.Lcd.println("HTTP server started");  
}

void web_server_handle_client(void)
{
  server.handleClient();
}

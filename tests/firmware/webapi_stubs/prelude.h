#pragma once
// Suppress hardware-heavy declarations, not any production .cpp branches.
#define _WEB_API_H
#define _ROBOT_H
#define _CHAT_GPT_H
#define _FUNCTION_CALL_H
#define _PLAY_MP3_H
#define _CAMERA_H_
#define ENABLE_CAMERA
#include "Arduino.h"
#include "M5Unified.h"
#include "Avatar.h"
#include <ArduinoJson.h>
#include "share/ConversationSession.h"
inline void convertFromJson(ArduinoJson::JsonVariantConst src, String& dst) { dst = src.as<const char*>(); }
inline bool canConvertFromJson(ArduinoJson::JsonVariantConst src, const String&) { return src.is<const char*>(); }
struct EndpointConfig { String base_url = "http://fixture.invalid"; };
struct RobotConfig {
  struct ExConfig { EndpointConfig llm, stt, tts; } values;
  const ExConfig& getExConfig() const { return values; }
};
class Robot {
 public:
  RobotConfig m_config;
  int chatCalls = 0, speechCalls = 0;
  void chat(String) { ++chatCalls; }
  void speech(String) { ++speechCalls; }
};
extern Robot* robot;
extern bool isSubWindowON;
void mark_camera_trigger_awaiting(uint32_t timeout_ms = 8000);
void clear_camera_trigger_awaiting();
bool is_camera_trigger_active();
bool request_conversation_action(stackchan::ConversationSession& session);
void cancel_conversation_action();
bool conversation_action_owns(const stackchan::ConversationSession& session);
bool playMP3SD(const char*, bool = true, bool = false);
bool playMP3URL(const char*, bool = false, bool = true);
bool playLocalPrompt(const char*, bool = false);
void playInputErrorPrompt(bool = false, const char* = nullptr);
bool camera_capture_post_upload(const String&, const char*, const char*, const char*, const char*, const char*, const char* = "", const char* = "");

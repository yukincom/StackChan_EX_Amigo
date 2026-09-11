#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <new>
#include "rootCA/rootCACertificate.h"
#include "tts/OpenAITTS.h"
#include "SpiRamJsonDocument.h"
#include "driver/PlayMP3.h"
#include "AudioFileSourceHttpsPostStream.h"
#include "llm/ChatGPT/ChatGPT.h"    // for API key
#include "share/HttpUtil.h"

String OPENAITTS_MODEL = "tts-1";
String OPENAITTS_VOICE = "alloy";

static String dataTemplate = 
"{"
    "\"input\": \"dummy\","
    "\"model\": \"tts-1\","
    "\"voice\": \"alloy\","
    "\"speed\": 1,"
    "\"response_format\": \"mp3\""
"}";


void OpenAITTS::stream(String text){
  (void)streamImpl(text, false);
}

bool OpenAITTS::streamChecked(String text){
  return streamImpl(text, true);
}

bool OpenAITTS::streamImpl(String text, bool checkCompletion){
  if (text.isEmpty() || preallocateBuffer == nullptr || mp3 == nullptr) return false;
  String tts_url = param.base_url != ""
    ? join_url(param.base_url, "/v1/audio/speech")
    : String("https://api.openai.com/v1/audio/speech");

  const char* root_ca = is_https_url(tts_url) && param.base_url == "" ? root_ca_openai : nullptr;
  AudioFileSourceHttpsPostStream *stream = new (std::nothrow) AudioFileSourceHttpsPostStream(tts_url.c_str(), root_ca);
  if (stream == nullptr) return false;
  stream->addHeader("content-type", "application/json");
  if (param.api_key != "") {
    stream->addHeader("Authorization", String("Bearer ") + param.api_key);
  }
  Serial.println(dataTemplate);
  DynamicJsonDocument doc(2000);
  DeserializationError error = deserializeJson(doc, dataTemplate.c_str());
  if (error) {
    Serial.println("DeserializationError");
    String jsonStr;
    serializeJsonPretty(doc, jsonStr);  // 文字列をシリアルポートに出力する
    Serial.println(jsonStr);
    delete stream;
    return false;
  }
  doc["input"] = String(text);
  if(param.model != 0){
    doc["model"] = param.model;
  }
  if(param.voice != 0){
    doc["voice"] = param.voice;
  }
  String jsonStr;
  serializeJson(doc, jsonStr);
  if (doc.overflowed()) {
    delete stream;
    return false;
  }
  stream->addData(jsonStr);
  if (!stream->open(tts_url.c_str())) {
    delete stream;
    return false;
  }

  AudioFileSourceBuffer *buff = new (std::nothrow) AudioFileSourceBuffer(stream, preallocateBuffer, preallocateBufferSize);
  if (buff == nullptr) {
    delete stream;
    return false;
  }
  const bool played = playMP3(buff, checkCompletion);
  delete buff;
  delete stream;
  return played;  // Checked playback validates full body before closing it.
}

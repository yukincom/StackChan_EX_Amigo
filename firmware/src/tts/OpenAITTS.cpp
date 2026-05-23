#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
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
  String tts_url = param.base_url != ""
    ? join_url(param.base_url, "/v1/audio/speech")
    : String("https://api.openai.com/v1/audio/speech");

  const char* root_ca = is_https_url(tts_url) && param.base_url == "" ? root_ca_openai : nullptr;
  AudioFileSourceHttpsPostStream *stream = new AudioFileSourceHttpsPostStream(tts_url.c_str(), root_ca);
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
    return;
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
  stream->addData(jsonStr);
  stream->open(tts_url.c_str());

  AudioFileSourceBuffer *buff = new AudioFileSourceBuffer(stream, preallocateBuffer, preallocateBufferSize);
  playMP3(buff);
  delete stream;
  delete buff;
}

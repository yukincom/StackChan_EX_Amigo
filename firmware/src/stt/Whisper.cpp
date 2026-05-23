#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include "Whisper.h"
#include "rootCA/rootCACertificate.h"
#include "share/HttpUtil.h"

namespace {
constexpr char* DEFAULT_API_URL = "https://api.openai.com/v1/audio/transcriptions";
}

Whisper::Whisper(stt_param_t param) : STTBase(param) {
}

Whisper::~Whisper() {
}

String Whisper::Transcribe(AudioWhisper* audio) {
  clearLastError();
  String api_url = param.stt_conf.base_url != ""
    ? join_url(param.stt_conf.base_url, "/v1/audio/transcriptions")
    : String(DEFAULT_API_URL);
  bool use_tls = is_https_url(api_url);

  char boundary[64] = "------------------------";
  for (auto i = 0; i < 2; ++i) {
    ltoa(random(0x7fffffff), boundary + strlen(boundary), 16);
  }

  String header = "--" + String(boundary) + "\r\n"
    "Content-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
    "--" + String(boundary) + "\r\n"
    "Content-Disposition: form-data; name=\"language\"\r\n\r\nja\r\n"
    "--" + String(boundary) + "\r\n"
    "Content-Disposition: form-data; name=\"file\"; filename=\"speak.wav\"\r\n"
    "Content-Type: application/octet-stream\r\n\r\n";
  String footer = "\r\n--" + String(boundary) + "--\r\n";

  int body_size = header.length() + audio->GetSize() + footer.length();
  uint8_t *body = (uint8_t *)malloc(body_size);
  if (!body) {
    Serial.println("Whisper: failed to allocate request body");
    lastError = STTErrorType::Http;
    return "";
  }

  int offset = 0;
  memcpy(body + offset, header.c_str(), header.length());
  offset += header.length();
  memcpy(body + offset, audio->GetBuffer(), audio->GetSize());
  offset += audio->GetSize();
  memcpy(body + offset, footer.c_str(), footer.length());

  String payload = "";
  int httpCode = 0;

  if (use_tls) {
    WiFiClientSecure client;
    if (param.stt_conf.base_url == "") {
      client.setCACert(root_ca_openai);
    } else {
      client.setInsecure();
    }

    HTTPClient http;
    http.setTimeout(15000);
    if (!http.begin(client, api_url)) {
      free(body);
      Serial.println("Whisper: Connection failed!");
      lastError = STTErrorType::Connection;
      return "";
    }

    http.addHeader("Content-Type", String("multipart/form-data; boundary=") + boundary);
    if (param.api_key != "") {
      http.addHeader("Authorization", String("Bearer ") + param.api_key);
    }

    httpCode = http.POST(body, body_size);
    if (httpCode > 0) {
      payload = http.getString();
    } else {
      Serial.printf("Whisper: POST failed: %s\n", http.errorToString(httpCode).c_str());
    }
    http.end();
  } else {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(15000);
    if (!http.begin(client, api_url)) {
      free(body);
      Serial.println("Whisper: Connection failed!");
      lastError = STTErrorType::Connection;
      return "";
    }

    http.addHeader("Content-Type", String("multipart/form-data; boundary=") + boundary);
    if (param.api_key != "") {
      http.addHeader("Authorization", String("Bearer ") + param.api_key);
    }

    httpCode = http.POST(body, body_size);
    if (httpCode > 0) {
      payload = http.getString();
    } else {
      Serial.printf("Whisper: POST failed: %s\n", http.errorToString(httpCode).c_str());
    }
    http.end();
  }

  free(body);
  if (httpCode <= 0) {
    lastError = STTErrorType::Http;
    return "";
  }
  if (httpCode >= 400) {
    Serial.printf("Whisper: server returned HTTP %d\n", httpCode);
    lastError = (httpCode >= 500) ? STTErrorType::Connection : STTErrorType::NoSpeech;
    return "";
  }

  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, payload);
  if (error) {
    Serial.printf("Whisper: JSON parse failed: %s\n", error.c_str());
    lastError = STTErrorType::Parse;
    return "";
  }

  if (!doc["text"].is<const char*>()) {
    lastError = STTErrorType::NoSpeech;
    return "";
  }

  String result = doc["text"].as<String>();
  if (result == "" || result == "null") {
    lastError = STTErrorType::NoSpeech;
    return "";
  }
  return result;
}

String Whisper::speech_to_text(){
  String ret;
  AudioWhisper* audio = new AudioWhisper();
  Serial.println("\r\nRecord start!\r\n");
  audio->Record();
  Serial.println("Record end\r\n");
  Serial.println("音声認識開始");
  ret = Transcribe(audio);
  delete audio;
  return ret;
}

#include <Arduino.h>
#include <M5Unified.h>
#include <SD.h>
#include <SPIFFS.h>
#include <HTTPClient.h>
#include <AudioOutput.h>
#include <AudioFileSourceBuffer.h>
#include <AudioGeneratorMP3.h>
#include <new>
#include "AudioFileSourceHTTPSStream.h"
#include "AudioFileSourceSD.h"
#include "AudioFileSourceSPIFFS.h"
#include "AudioOutputM5Speaker.h"
#include "PlayMP3.h"
#include "Mp3Playback.h"
#include "Avatar.h"
#include "share/IdleExpression.h"

using namespace m5avatar;

extern Avatar avatar;
extern bool servo_home;

/// set M5Speaker virtual channel (0-7)
//static constexpr uint8_t m5spk_virtual_channel = 0;
uint8_t m5spk_virtual_channel = 0;

AudioOutputM5Speaker out(&M5.Speaker, m5spk_virtual_channel);
AudioGeneratorMP3 *mp3 = nullptr;
static bool s_skip_next_tts_speech = false;

int preallocateBufferSize = 30*1024;
uint8_t *preallocateBuffer = nullptr;




void mp3_init(void)
{
    mp3 = new (std::nothrow) AudioGeneratorMP3();
    //out = new AudioOutputM5Speaker(&M5.Speaker, m5spk_virtual_channel);

    // TTS MP3用バッファ。PSRAMを優先し、利用できないボードでは通常RAMへフォールバックする。
    preallocateBuffer = static_cast<uint8_t*>(
        heap_caps_malloc(preallocateBufferSize, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (preallocateBuffer == nullptr) {
        preallocateBuffer = static_cast<uint8_t*>(
            heap_caps_malloc(preallocateBufferSize, MALLOC_CAP_8BIT));
    }
    if (mp3 == nullptr || preallocateBuffer == nullptr) {
        M5.Display.printf("MP3 init failed (decoder=%s, buffer=%s)\n",
                         mp3 != nullptr ? "ok" : "ng",
                         preallocateBuffer != nullptr ? "ok" : "ng");
        Serial.println("MP3 playback is disabled because initialization failed");
    }

    audioLogger = &Serial;
}

bool playMP3(AudioFileSourceBuffer *buff, bool checkCompletion){

  if (mp3 == nullptr || preallocateBuffer == nullptr || buff == nullptr) {
    Serial.println("MP3 playback is unavailable");
    return false;
  }

  M5.Mic.end();
  M5.Speaker.begin();
  Serial.println("mp3 start");
  const bool result = stackchan::decodeMp3(*mp3, *buff, out, checkCompletion);
  Serial.println("mp3 stop");

  M5.Speaker.end();
  M5.Mic.begin();
  return result;
}

bool playMP3SPIFFS(const char *filename)
{
  bool result;

  if (mp3 == nullptr || preallocateBuffer == nullptr) {
    return false;
  }

  if (SPIFFS.exists(filename)) {
    AudioFileSourceSPIFFS *file_mp3 = new (std::nothrow) AudioFileSourceSPIFFS(filename);
    Serial.println("Open mp3");
    
    if (file_mp3 == nullptr || !file_mp3->isOpen()) {
      delete file_mp3;
      file_mp3 = nullptr;
      Serial.println("failed to open mp3 file");
      result = false;
    }
    else{
      AudioFileSourceBuffer *buff = new (std::nothrow) AudioFileSourceBuffer(
          file_mp3, preallocateBuffer, preallocateBufferSize);
      if (buff == nullptr) {
        delete file_mp3;
        Serial.println("failed to allocate mp3 source buffer");
        return false;
      }
      setActivityExpression(Expression::Happy);
      servo_home = false;

      result = playMP3(buff);
      
      setIdleExpression();
      servo_home = true;

      delete buff;
      delete file_mp3;
    }
  }else{
    Serial.println("mp3 file is not exist");
    result = false;
  }
  return result;
}


bool playMP3SD(const char *filename, bool useTalkingExpression)
{
  bool result;

  if (mp3 == nullptr || preallocateBuffer == nullptr) {
    return false;
  }

  if (SD.exists(filename)) {

    AudioFileSourceSD *file_mp3 = new (std::nothrow) AudioFileSourceSD(filename);
    Serial.println("Open mp3");
    
    if (file_mp3 == nullptr || !file_mp3->isOpen()) {
      delete file_mp3;
      //file_mp3 = nullptr;
      Serial.println("failed to open mp3 file");
      result = false;
    }
    else{
      AudioFileSourceBuffer *buff = new (std::nothrow) AudioFileSourceBuffer(
          file_mp3, preallocateBuffer, preallocateBufferSize);
      if (buff == nullptr) {
        delete file_mp3;
        Serial.println("failed to allocate mp3 source buffer");
        return false;
      }
      m5avatar::Expression prevExpression = avatar.getExpression();
      if (useTalkingExpression) {
        setActivityExpression(Expression::Happy);
      }
      servo_home = false;

      result = playMP3(buff);
      
      if (useTalkingExpression) {
        setIdleExpression();
      } else {
        avatar.setExpression(prevExpression);
      }
      servo_home = true;

      delete buff;
      delete file_mp3;
    }
  }else{
    Serial.println("mp3 file is not exist");
    result = false;
  }

  return result;
}

bool playMP3URL(const char *url)
{
  bool result = false;
  static constexpr const char *kTempPushMp3 = "/push_tmp.mp3";
  // SPIFFS 肥大・DoS 防止（push TTS の想定上限）
  static constexpr size_t kMaxPushMp3Bytes = 1024 * 1024;
  static constexpr uint32_t kDownloadIdleTimeoutMs = 5000;
  static constexpr uint32_t kDownloadDeadlineMs = 30000;

  if (url == nullptr || strlen(url) == 0) {
    Serial.println("mp3 url is empty");
    return false;
  }

  HTTPClient http;
  http.useHTTP10(true);
  http.setReuse(false);
  http.setTimeout(30000);
  if (!http.begin(url)) {
    Serial.println("failed to begin mp3 url");
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("failed to get mp3 url: %d\n", code);
    http.end();
    return false;
  }

  int content_length = http.getSize();
  if (content_length > 0 && static_cast<size_t>(content_length) > kMaxPushMp3Bytes) {
    Serial.printf("mp3 url too large: %d bytes\n", content_length);
    http.end();
    return false;
  }

  if (SPIFFS.exists(kTempPushMp3)) {
    SPIFFS.remove(kTempPushMp3);
  }

  File file_mp3 = SPIFFS.open(kTempPushMp3, FILE_WRITE);
  if (!file_mp3) {
    Serial.println("failed to create temp mp3");
    http.end();
    return false;
  }

  WiFiClient *stream = http.getStreamPtr();
  uint8_t buffer[1024];
  size_t total_written = 0;
  bool aborted = false;
  uint32_t download_started_at = millis();
  uint32_t last_progress_at = millis();
  while (http.connected() || stream->available() > 0) {
    if (static_cast<uint32_t>(millis() - download_started_at) >= kDownloadDeadlineMs) {
      Serial.println("mp3 download exceeded total time limit");
      aborted = true;
      break;
    }
    size_t available = stream->available();
    if (available == 0) {
      if (content_length > 0 && total_written >= static_cast<size_t>(content_length)) {
        break;
      }
      if (content_length <= 0 && !stream->connected()) {
        break;
      }
      if (static_cast<uint32_t>(millis() - last_progress_at) >= kDownloadIdleTimeoutMs) {
        Serial.println("mp3 download timed out while waiting for data");
        aborted = true;
        break;
      }
      delay(1);
      continue;
    }
    size_t chunk = available < sizeof(buffer) ? available : sizeof(buffer);
    int read_len = stream->readBytes(buffer, chunk);
    if (read_len <= 0) {
      aborted = true;
      break;
    }
    last_progress_at = millis();
    total_written += static_cast<size_t>(read_len);
    if (total_written > kMaxPushMp3Bytes) {
      Serial.println("mp3 download exceeded size cap");
      aborted = true;
      break;
    }
    size_t written = file_mp3.write(buffer, static_cast<size_t>(read_len));
    if (written != static_cast<size_t>(read_len)) {
      Serial.printf("mp3 file write failed: %u of %d bytes\n",
                    static_cast<unsigned>(written), read_len);
      aborted = true;
      break;
    }
  }
  file_mp3.close();
  http.end();

  if (content_length > 0 && total_written != static_cast<size_t>(content_length)) {
    Serial.printf("mp3 download truncated: %u of %d bytes\n",
                  static_cast<unsigned>(total_written), content_length);
    aborted = true;
  }
  if (total_written == 0) {
    Serial.println("mp3 download returned an empty body");
    aborted = true;
  }

  if (aborted) {
    SPIFFS.remove(kTempPushMp3);
    return false;
  }

  if (!SPIFFS.exists(kTempPushMp3)) {
    Serial.println("temp mp3 missing after download");
    return false;
  }

  result = playMP3SPIFFS(kTempPushMp3);
  SPIFFS.remove(kTempPushMp3);
  return result;
}

bool playLocalPrompt(const char *endpoint)
{
  if (endpoint == nullptr || strlen(endpoint) == 0) {
    return false;
  }

  String sd_path = "/stack_sd_audio/";
  sd_path += endpoint;
  sd_path += ".mp3";

  bool useTalkingExpression = strcmp(endpoint, "hm") == 0;
  return playMP3SD(sd_path.c_str(), useTalkingExpression);
}

void markSkipNextTtsSpeech(void)
{
  s_skip_next_tts_speech = true;
}

bool consumeSkipNextTtsSpeech(void)
{
  bool value = s_skip_next_tts_speech;
  s_skip_next_tts_speech = false;
  return value;
}

#include <Arduino.h>
#include <M5Unified.h>
#include <SD.h>
#include <SPIFFS.h>
#include <HTTPClient.h>
#include <AudioOutput.h>
#include <AudioFileSourceBuffer.h>
#include <AudioGeneratorMP3.h>
#include "AudioFileSourceHTTPSStream.h"
#include "AudioFileSourceSD.h"
#include "AudioFileSourceSPIFFS.h"
#include "AudioOutputM5Speaker.h"
#include "PlayMP3.h"
#include "Avatar.h"

using namespace m5avatar;

extern Avatar avatar;
extern bool servo_home;

/// set M5Speaker virtual channel (0-7)
//static constexpr uint8_t m5spk_virtual_channel = 0;
uint8_t m5spk_virtual_channel = 0;

AudioOutputM5Speaker out(&M5.Speaker, m5spk_virtual_channel);
AudioGeneratorMP3 *mp3;
static bool s_skip_next_tts_speech = false;

int preallocateBufferSize = 30*1024;
uint8_t *preallocateBuffer;




void mp3_init(void)
{
    mp3 = new AudioGeneratorMP3();
    //out = new AudioOutputM5Speaker(&M5.Speaker, m5spk_virtual_channel);

    //TTS MP3用バッファ （PSRAMから確保される）
    preallocateBuffer = (uint8_t *)malloc(preallocateBufferSize);
    if (!preallocateBuffer) {
        M5.Display.printf("FATAL ERROR:  Unable to preallocate %d bytes for app\n", preallocateBufferSize);
        for (;;) { delay(1000); }
    }

    audioLogger = &Serial;
}

void playMP3(AudioFileSourceBuffer *buff){

  M5.Mic.end();
  M5.Speaker.begin();

  mp3->begin(buff, &out);
  Serial.println("mp3 start");

  while(mp3->isRunning()) {
    if (!mp3->loop()) {
      mp3->stop();
      Serial.println("mp3 stop");
    }
    delay(1);
  }

  M5.Speaker.end();
  M5.Mic.begin();

}

bool playMP3SPIFFS(const char *filename)
{
  bool result;

  if (SPIFFS.exists(filename)) {
    AudioFileSourceSPIFFS *file_mp3 = new AudioFileSourceSPIFFS(filename);
    Serial.println("Open mp3");
    
    if( !file_mp3->isOpen() ){
      delete file_mp3;
      file_mp3 = nullptr;
      Serial.println("failed to open mp3 file");
      result = false;
    }
    else{
      AudioFileSourceBuffer *buff = new AudioFileSourceBuffer(file_mp3, preallocateBuffer, preallocateBufferSize);
      avatar.setExpression(Expression::Happy);
      servo_home = false;

      playMP3(buff);
      
      avatar.setExpression(Expression::Neutral);
      servo_home = true;

      delete file_mp3;
      delete buff;
      result = true;
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

  if (SD.exists(filename)) {

    AudioFileSourceSD *file_mp3 = new AudioFileSourceSD(filename);
    Serial.println("Open mp3");
    
    if( !file_mp3->isOpen() ){
      delete file_mp3;
      //file_mp3 = nullptr;
      Serial.println("failed to open mp3 file");
      result = false;
    }
    else{
      AudioFileSourceBuffer *buff = new AudioFileSourceBuffer(file_mp3, preallocateBuffer, preallocateBufferSize);
      m5avatar::Expression prevExpression = avatar.getExpression();
      if (useTalkingExpression) {
        avatar.setExpression(Expression::Happy);
      }
      servo_home = false;

      playMP3(buff);
      
      avatar.setExpression(useTalkingExpression ? Expression::Neutral : prevExpression);
      servo_home = true;

      delete file_mp3;
      delete buff;
      result = true;
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
  while (http.connected()) {
    size_t available = stream->available();
    if (available == 0) {
      if (http.getSize() == file_mp3.size()) {
        break;
      }
      delay(1);
      continue;
    }
    size_t chunk = available < sizeof(buffer) ? available : sizeof(buffer);
    int read_len = stream->readBytes(buffer, chunk);
    if (read_len <= 0) {
      break;
    }
    file_mp3.write(buffer, read_len);
  }
  file_mp3.close();
  http.end();

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

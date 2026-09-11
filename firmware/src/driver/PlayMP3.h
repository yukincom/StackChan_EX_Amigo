#ifndef _PLAY_MP3_H
#define _PLAY_MP3_H

#include <Arduino.h>
#include <M5Unified.h>
#include <AudioFileSourceBuffer.h>
#include <AudioGeneratorMP3.h>
#include "AudioFileSourceHTTPSStream.h"
#include "AudioOutputM5Speaker.h"

extern uint8_t m5spk_virtual_channel;

extern AudioOutputM5Speaker out;
//extern AudioOutputM5Speaker *out;
extern AudioGeneratorMP3 *mp3;
extern AudioFileSourceBuffer *buff;
extern AudioFileSourceHTTPSStream *file;
extern int preallocateBufferSize;
extern uint8_t *preallocateBuffer;

extern void mp3_init(void);
extern bool playMP3(AudioFileSourceBuffer *buff, bool checkCompletion = false);
extern bool playMP3SPIFFS(const char *filename, bool checkCompletion = false, bool useTalkingExpression = true);
extern bool playMP3SD(const char *filename, bool useTalkingExpression = true, bool checkCompletion = false);
extern bool playMP3URL(const char *url, bool checkCompletion = false, bool useTalkingExpression = true);
extern bool playLocalPrompt(const char *endpoint);
extern void playInputErrorPrompt(bool connectionError = false);
extern void markSkipNextTtsSpeech(void);
extern bool consumeSkipNextTtsSpeech(void);

#endif

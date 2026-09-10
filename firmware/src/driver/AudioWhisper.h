#ifndef _AUDIOWHISPER_H
#define _AUDIOWHISPER_H

#include <Arduino.h>

class AudioWhisper {
  byte* record_buffer;
  bool cancelled = false;
 public:
  AudioWhisper();
  ~AudioWhisper();

  const byte* GetBuffer() const { return record_buffer; }
  size_t GetSize() const;
  bool IsReady() const { return record_buffer != nullptr; }

  bool Record(bool cancelOnTouch = false);
  bool WasCancelled() const { return cancelled; }
};

#endif // _AUDIOWHISPER_H

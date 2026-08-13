#ifndef _AUDIOWHISPER_H
#define _AUDIOWHISPER_H

#include "AudioWhisper.h"

class AudioWhisper {
  byte* record_buffer;
 public:
  AudioWhisper();
  ~AudioWhisper();

  const byte* GetBuffer() const { return record_buffer; }
  size_t GetSize() const;
  bool IsReady() const { return record_buffer != nullptr; }

  bool Record();
};

#endif // _AUDIOWHISPER_H

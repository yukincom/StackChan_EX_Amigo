#pragma once
#include <cstdint>
class AudioOutput {
 public:
  virtual ~AudioOutput() = default;
  virtual bool SetRate(int hz) { hertz = hz; return true; }
  virtual bool begin() = 0;
  virtual bool ConsumeSample(int16_t sample[2]) = 0;
  virtual void flush() = 0;
  virtual bool stop() = 0;
  uint32_t hertz = 16000;
};

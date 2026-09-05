#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace stackchan {

inline int pcmRms(const int16_t* samples, size_t count) {
  if (!samples || count == 0) return 0;
  uint64_t squares = 0;
  for (size_t i = 0; i < count; ++i) {
    const int64_t sample = samples[i];
    squares += static_cast<uint64_t>(sample * sample);
  }
  return static_cast<int>(std::sqrt(static_cast<double>(squares) / count));
}

// Initial tuning: keep the previous noise floor and full-open level, but use
// a 20 ms envelope instead of sampling an arbitrary PCM value every 100 ms.
class MouthEnvelope {
 public:
  float update(int rms) {
    float target = rms < 100 ? 0.0f : static_cast<float>(rms) / 15000.0f;
    if (target > 1.0f) target = 1.0f;
    const float rate = target > open_ ? 0.65f : 0.35f;
    open_ += rate * (target - open_);
    if (target == 0.0f && open_ < 0.005f) open_ = 0.0f;
    return open_;
  }

 private:
  float open_ = 0.0f;
};

}  // namespace stackchan

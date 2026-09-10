#include <cassert>
#include <cstddef>
#include <cstdint>
namespace m5 {
class Speaker_Class {
 public:
  bool accepted = true;
  int queued = 0;
  bool stuck = false;
  bool playRaw(const int16_t*, size_t, uint32_t, bool, int, uint8_t) { return accepted; }
  void stop(uint8_t) {}
  bool isPlaying(uint8_t) { return stuck || queued-- > 0; }
};
}
uint32_t now = 100;
uint32_t millis() { return now; }
void delay(uint32_t ms) { now += ms; }
#include "driver/AudioOutputM5Speaker.h"

int main() {
  int16_t silence[] = {0, 0};
  int16_t full[] = {-32768, -32768};
  int16_t wave[] = {0, 1000, 0, -1000};
  assert(stackchan::pcmRms(silence, 2) == 0);
  assert(stackchan::pcmRms(full, 2) == 32768);
  assert(stackchan::pcmRms(wave, 4) == 707);
  m5::Speaker_Class speaker;
  AudioOutputM5Speaker out(&speaker);
  out.begin();
  assert(out.getLevel() == 0);
  assert(!out.playbackDelivered());
  for (int16_t value : wave) {
    int16_t sample[] = {value, value};
    assert(out.ConsumeSample(sample));
  }
  out.flush();
  assert(out.getLevel() == 707);  // The first sample is zero, but the block is not silent.
  now += 121;
  assert(out.getLevel() == 0);
  int16_t sample[] = {1000, 1000};
  out.ConsumeSample(sample);
  out.flush();
  assert(out.getLevel() == 1000);
  out.stop();
  assert(out.getLevel() == 0);
  assert(out.playbackDelivered());
  speaker.accepted = false;
  out.ConsumeSample(sample);
  out.flush();
  assert(out.getLevel() == 0);
  assert(!out.playbackDelivered());
  speaker.accepted = true;
  out.begin();
  out.setDrainOnStop(true);
  out.ConsumeSample(sample);
  speaker.queued = 4;
  uint32_t beforeDrain = now;
  out.stop();
  assert(now - beforeDrain == 4);  // No artificial two-second delay.
  assert(out.playbackDelivered());
  out.begin();
  out.ConsumeSample(sample);
  speaker.stuck = true;
  beforeDrain = now;
  out.stop();
  assert(now - beforeDrain == 2000);
  assert(!out.playbackDelivered());
  stackchan::MouthEnvelope mouth;
  assert(mouth.update(0) == 0.0f);
  float open = mouth.update(15000);
  assert(open > 0.0f && open < 1.0f);
  assert(mouth.update(32768) <= 1.0f);
  assert(mouth.update(0) < 1.0f);
  for (int i = 0; i < 30; ++i) open = mouth.update(0);
  assert(open == 0.0f);
}

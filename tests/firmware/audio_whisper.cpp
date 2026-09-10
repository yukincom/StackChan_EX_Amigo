#include <cassert>
#include <initializer_list>
#include <M5Unified.h>
#include "driver/AudioWhisper.h"

FakeSerial Serial;
FakeM5 M5;
static uint32_t now = 0;
static int allocations = 0;
static bool failAllocation = false;
uint32_t millis() { return now; }
void delay(uint32_t ms) { now += ms; }
void* heap_caps_malloc(size_t size, int) {
  assert(size == 120044);  // Same single 3.75-second PCM/WAV buffer.
  if (failAllocation) return nullptr;
  ++allocations;
  return std::malloc(size);
}
void heap_caps_free(void* data) {
  assert(!M5.Mic.active);  // Never free a buffer still owned by the mic.
  --allocations;
  std::free(data);
}
bool FakeMic::record(int16_t* data, size_t length, size_t rate) {
  assert(active && length == 150 && rate == 16000);
  if (recordCalls++ == failChunk) return false;
  std::memset(data, 0, length * sizeof(int16_t));
  return true;
}

int main() {
  {
    AudioWhisper audio;
    assert(audio.IsReady() && allocations == 1);
    const byte* original = audio.GetBuffer();
    M5.Touch.count = 1;
    assert(audio.Record());  // Ordinary touch-triggered capture is unchanged.
    assert(!audio.WasCancelled() && M5.updates == 0);
    assert(M5.Mic.recordCalls == 400 && M5.Mic.endCalls == 1 && now == 3);
    assert(!std::memcmp(audio.GetBuffer(), "RIFF", 4));
    assert(!std::memcmp(audio.GetBuffer() + 8, "WAVE", 4));
    for (int touchAt : {1, 200, 401}) {
      M5 = FakeM5{};
      M5.touchAtUpdate = touchAt;
      assert(!audio.Record(true));
      assert(audio.WasCancelled() && M5.Mic.endCalls == 1);
      assert(M5.Mic.recordCalls == touchAt - 1);
      assert(!M5.Mic.active);
      assert(audio.GetBuffer() == original && allocations == 1);
    }
    M5 = FakeM5{};
    assert(audio.Record(true) && !audio.WasCancelled());
    assert(M5.Mic.recordCalls == 400);
    M5 = FakeM5{};
    M5.Mic.beginOk = false;
    assert(!audio.Record(true) && !audio.WasCancelled());
    assert(M5.Mic.recordCalls == 0);
    M5 = FakeM5{};
    M5.Mic.failChunk = 100;
    assert(!audio.Record(true) && !audio.WasCancelled());
    assert(M5.Mic.recordCalls == 101 && M5.Mic.endCalls == 1);
    M5 = FakeM5{};
    M5.Mic.neverDrains = true;
    now = UINT32_MAX - 1000;
    assert(!audio.Record(true) && !audio.WasCancelled());
    assert(uint32_t(now - (UINT32_MAX - 1000)) == 2000);
    assert(!M5.Mic.active && M5.Mic.endCalls == 1);
  }
  assert(allocations == 0);
  M5 = FakeM5{};
  failAllocation = true;
  AudioWhisper unavailable;
  assert(!unavailable.IsReady() && !unavailable.Record(true));
  assert(!unavailable.WasCancelled() && allocations == 0);
}

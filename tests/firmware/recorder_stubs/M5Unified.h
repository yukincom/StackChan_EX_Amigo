#pragma once
#include "Arduino.h"

struct FakeMic {
  bool beginOk = true;
  bool active = false;
  int recordCalls = 0;
  int failChunk = -1;
  int drainPolls = 3;
  bool neverDrains = false;
  int endCalls = 0;
  bool begin() { active = beginOk; return beginOk; }
  bool record(int16_t* data, size_t length, size_t rate);
  bool isRecording() { return active && (neverDrains || drainPolls-- > 0); }
  void end() { active = false; ++endCalls; }
};
struct FakeTouch {
  unsigned count = 0;
  unsigned getCount() const { return count; }
};
struct FakeM5 {
  FakeMic Mic;
  FakeTouch Touch;
  int updates = 0;
  int touchAtUpdate = -1;
  void update() {
    if (++updates == touchAtUpdate) Touch.count = 1;
  }
};
extern FakeM5 M5;

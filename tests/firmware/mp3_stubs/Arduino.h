#pragma once

#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include "pgmspace.h"

uint32_t millis();
void delay(uint32_t milliseconds);
inline void yield() {}

// Only the Arduino logging surface is replaced. Audio classes come from the
// installed ESP8266Audio dependency, not the simpler unit-test AudioOutput stub.
class Print {
 public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t) = 0;
  int printf_P(const char*, ...) { return 0; }
};

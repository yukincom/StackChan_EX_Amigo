#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>

using byte = unsigned char;
constexpr int MALLOC_CAP_SPIRAM = 1;
constexpr int MALLOC_CAP_8BIT = 2;
void* heap_caps_malloc(size_t size, int caps);
void heap_caps_free(void* data);
uint32_t millis();
void delay(uint32_t ms);
struct FakeSerial {
  void println(const char*) {}
  template <typename... Args> void printf(const char*, Args...) {}
};
extern FakeSerial Serial;

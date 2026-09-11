#pragma once
#include <cstdint>
#include <cstdlib>
#include <string>
#include <utility>
#define PROGMEM
class String {
 public:
  String() = default;
  String(const char* text) : value_(text ? text : "") {}
  String(std::string text) : value_(std::move(text)) {}
  String(int value) : value_(std::to_string(value)) {}
  const char* c_str() const { return value_.c_str(); }
  size_t length() const { return value_.size(); }
  bool isEmpty() const { return value_.empty(); }
  int indexOf(char value) const { const auto p = value_.find(value); return p == std::string::npos ? -1 : int(p); }
  int indexOf(const char* value) const { const auto p = value_.find(value); return p == std::string::npos ? -1 : int(p); }
  long toInt() const { return std::strtol(value_.c_str(), nullptr, 10); }
  String& operator+=(const String& text) { value_ += text.value_; return *this; }
  friend String operator+(String left, const String& right) { left += right; return left; }
  friend bool operator==(const String& a, const String& b) { return a.value_ == b.value_; }
  friend bool operator!=(const String& a, const String& b) { return !(a == b); }
 private:
  std::string value_;
};
uint32_t millis();
void delay(uint32_t);
struct SerialStub {
  template<class T> void println(const T&) {}
  template<class... T> void printf(const char*, T...) {}
};
extern SerialStub Serial;

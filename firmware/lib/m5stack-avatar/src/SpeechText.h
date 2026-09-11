#pragma once

#include <mutex>
#include <string>

namespace m5avatar {

// Own the caller's text and give each drawing frame an independent snapshot.
// Lock only while copying/updating text, never while rendering the frame.
class SpeechText {
 public:
  SpeechText() = default;
  SpeechText(const SpeechText& other) : text_(other.snapshot()) {}
  SpeechText& operator=(const SpeechText& other) {
    if (this != &other) {
      const std::string text = other.snapshot();
      set(text.c_str());
    }
    return *this;
  }

  // The input only needs to remain valid for the duration of this call.
  void set(const char* text) {
    std::lock_guard<std::mutex> lock(mutex_);
    text_ = text ? text : "";
  }

  std::string snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return text_;
  }

 private:
  mutable std::mutex mutex_;
  std::string text_;
};

}  // namespace m5avatar

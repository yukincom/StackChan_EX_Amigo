#pragma once

#include <stdint.h>

namespace stackchan {

// No timers/tasks or allocations. A handled hold consumes the entire contact,
// including its release and any subsequent flick by the same finger.
class TouchGesture {
 public:
  enum class Event { None, Tap, LongPress };
  static constexpr uint32_t HoldMs = 2000;
  static constexpr int MoveTolerance = 16;

  Event update(uint32_t now, unsigned count, int16_t x, int16_t y, bool busy) {
    if (count == 0) {
      const bool tap = tracking_ && !busy && !blocked_ && !moved_ && !handled_;
      tracking_ = false;
      return tap ? Event::Tap : Event::None;
    }
    if (!tracking_) {
      tracking_ = true;
      blocked_ = moved_ = handled_ = holdSent_ = false;
      started_ = now;
      startX_ = x;
      startY_ = y;
    }
    // A busy/multi-finger contact never becomes a tap when it ends.
    blocked_ = blocked_ || busy || count != 1;
    const int dx = int(x) - startX_;
    const int dy = int(y) - startY_;
    moved_ = moved_ || dx > MoveTolerance || dx < -MoveTolerance
                    || dy > MoveTolerance || dy < -MoveTolerance;
    if (!blocked_ && !moved_ && !holdSent_ && uint32_t(now - started_) >= HoldMs) {
      holdSent_ = true;
      return Event::LongPress;
    }
    return Event::None;
  }

  void consume() { handled_ = true; }
  void cancel() { blocked_ = true; }
  bool canFlick() const { return tracking_ && !blocked_ && !handled_; }
  int16_t startX() const { return startX_; }
  int16_t startY() const { return startY_; }

 private:
  bool tracking_ = false;
  bool blocked_ = false;
  bool moved_ = false;
  bool handled_ = false;
  bool holdSent_ = false;
  uint32_t started_ = 0;
  int16_t startX_ = 0;
  int16_t startY_ = 0;
};

}  // namespace stackchan

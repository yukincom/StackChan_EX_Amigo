#pragma once

namespace stackchan {

// Fixed screen coordinates: the drawn white rectangles ARE the hit targets.
class InputModeMenu {
 public:
  enum class Action { None, SelectTouch, SelectAlways };
  static constexpr int Left = 16;
  static constexpr int TouchTop = 16;
  static constexpr int AlwaysTop = 128;
  static constexpr int Width = 288;
  static constexpr int Height = 96;

  void open() { open_ = true; ready_ = false; }
  void close() { open_ = false; ready_ = false; }
  bool isOpen() const { return open_; }

  // The opening hold cannot select anything. Release first, then select on
  // the DOWN edge of a new single-finger contact, not on its later release.
  Action contact(unsigned count, int x, int y) {
    if (!open_) return Action::None;
    if (count == 0) {
      ready_ = true;
      return Action::None;
    }
    if (!ready_) return Action::None;
    ready_ = false;  // One decision per contact, including outside/multi-touch.
    if (count != 1) return Action::None;
    if (x >= Left && x < Left + Width) {
      if (y >= TouchTop && y < TouchTop + Height) {
        close();
        return Action::SelectTouch;
      }
      if (y >= AlwaysTop && y < AlwaysTop + Height) {
        close();
        return Action::SelectAlways;
      }
    }
    return Action::None;  // The gap/border does not close or select a mode.
  }

  template <class Canvas>
  static void drawButtons(Canvas& canvas) {
    canvas.fillScreen(0x0000);
    canvas.setTextColor(0x0000, 0xffff);
    canvas.setTextSize(1);
    canvas.fillRect(Left, TouchTop, Width, Height, 0xffff);
    canvas.drawString("タッチ", Left + Width / 2, TouchTop + Height / 2);
    canvas.fillRect(Left, AlwaysTop, Width, Height, 0xffff);
    canvas.drawString("いつでも", Left + Width / 2, AlwaysTop + Height / 2);
  }

 private:
  bool open_ = false;
  bool ready_ = false;
};

}  // namespace stackchan

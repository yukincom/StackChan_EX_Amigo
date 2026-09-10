#pragma once

namespace stackchan {

// CoreS3/Core2 320x240 screen. Text uses the existing 20px SubWindow font.
class InputModeMenu {
 public:
  enum class Action { None, Close, SelectTouch, SelectAlways };
  static constexpr int Left = 20;
  static constexpr int Top = 20;
  static constexpr int Width = 280;
  static constexpr int Height = 200;

  void open() { open_ = true; }
  void close() { open_ = false; }
  bool isOpen() const { return open_; }
  Action tap(int x, int y) {
    if (!open_) return Action::None;
    if (x >= Left && x < Left + Width) {
      if (y >= Top + 40 && y < Top + 80) {
        close();
        return Action::SelectTouch;
      }
      if (y >= Top + 100 && y < Top + 140) {
        close();
        return Action::SelectAlways;
      }
    }
    close();
    return Action::Close;
  }

 private:
  bool open_ = false;
};

}  // namespace stackchan

#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <string>
#include <vector>
#include "share/TouchGesture.h"
#include "share/InputModeMenu.h"

using Gesture = stackchan::TouchGesture;
using Event = Gesture::Event;
using Menu = stackchan::InputModeMenu;
using Action = Menu::Action;

struct Canvas {
  struct Rect { int x, y, width, height, color; };
  struct Text { std::string value; int x, y; };
  int background = -1, foreground = -1, textBackground = -1;
  int textSize = -1, datum = -1;
  std::vector<Rect> rectangles;
  std::vector<Text> texts;
  void fillScreen(int color) { background = color; }
  void setTextColor(int front, int back) { foreground = front; textBackground = back; }
  void setTextSize(int size) { textSize = size; }
  void setTextDatum(int value) { datum = value; }
  void fillRect(int x, int y, int w, int h, int color) {
    rectangles.push_back({x, y, w, h, color});
  }
  void drawString(const char* value, int x, int y) { texts.push_back({value, x, y}); }
};

int main() {
  // Production drawing: two large white buttons, centered black labels only.
  Canvas canvas;
  Menu::drawButtons(canvas);
  assert(canvas.background == 0 && canvas.foreground == 0);
  assert(canvas.textBackground == 0xffff && canvas.textSize == 1 && canvas.datum == 4);
  assert(canvas.rectangles.size() == 2 && canvas.texts.size() == 2);
  for (unsigned i = 0; i < 2; ++i) {
    const auto& rect = canvas.rectangles[i];
    const auto& text = canvas.texts[i];
    assert(rect.x == 16 && rect.y == (i == 0 ? 16 : 128));
    assert(rect.width == 288 && rect.height == 96 && rect.color == 0xffff);
    assert(text.x == rect.x + rect.width / 2 && text.y == rect.y + rect.height / 2);
    assert(text.value == (i == 0 ? "タッチ" : "いつでも"));
  }

  // Every physical pixel agrees with the production white rectangles. The
  // opening finger, even on a button, never selects until released and repressed.
  for (int y = 0; y < 240; ++y) {
    for (int x = 0; x < 320; ++x) {
      Menu menu;
      menu.open();
      assert(menu.contact(1, x, y) == Action::None);
      assert(menu.contact(0, 0, 0) == Action::None);
      Action expected = Action::None;
      for (unsigned i = 0; i < 2; ++i) {
        const auto& rect = canvas.rectangles[i];
        if (x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height) {
          expected = i == 0 ? Action::SelectTouch : Action::SelectAlways;
        }
      }
      assert(menu.contact(1, x, y) == expected);
      assert(menu.isOpen() == (expected == Action::None));
    }
  }

  // Exact hold threshold and repeat selection of either mode, including rollover.
  for (uint32_t start : {uint32_t(100), UINT32_MAX - 1000}) {
    Gesture gesture;
    Menu menu;
    for (int choice : {176, 64, 64, 176}) {
      assert(gesture.update(start, 1, 30, 70, false) == Event::None);
      assert(gesture.update(start + 1999, 1, 30, 70, false) == Event::None);
      assert(gesture.update(start + 2000, 1, 30, 70, false) == Event::LongPress);
      menu.open();
      gesture.consume();
      assert(!gesture.canFlick());
      assert(menu.contact(1, 160, choice) == Action::None);
      assert(gesture.update(start + 2100, 1, 160, choice, true) == Event::None);
      gesture.cancel();
      // M5Unified's release-detail frame can have raw count 1, but pressed count 0.
      assert(menu.contact(0, 0, 0) == Action::None);
      assert(gesture.update(start + 2200, 1, 160, choice, true) == Event::None);
      gesture.cancel();
      assert(gesture.update(start + 2250, 0, 0, 0, true) == Event::None);
      assert(menu.isOpen());  // Opening release does not select either mode.
      assert(menu.contact(1, 160, choice) == (choice == 64 ? Action::SelectTouch : Action::SelectAlways));
      assert(!menu.isOpen());
      assert(gesture.update(start + 2300, 1, 160, choice, true) == Event::None);
      gesture.cancel();
      assert(!gesture.canFlick());
      // Menu has closed on DOWN; holding/releasing this finger cannot start STT
      // or reopen it. A fresh face hold works again in either selected mode.
      assert(gesture.update(start + 4500, 1, 160, choice, false) == Event::None);
      assert(gesture.update(start + 4600, 0, 0, 0, false) == Event::None);
      start += 5000;
    }
  }

  // Unhandled holds keep the old behavior in other modules, but fire once only.
  Gesture unused;
  unused.update(0, 1, 10, 10, false);
  assert(unused.update(2000, 1, 10, 10, false) == Event::LongPress);
  assert(unused.update(3000, 1, 10, 10, false) == Event::None);
  assert(unused.update(3100, 0, 0, 0, false) == Event::Tap);
  assert(unused.update(3200, 0, 0, 0, false) == Event::None);

  // Moving out and back cannot turn a wipe into a hold/tap. Flick stays available.
  Gesture moved;
  moved.update(0, 1, 40, 70, false);
  moved.update(100, 1, 57, 70, false);
  assert(moved.canFlick());
  assert(moved.update(2500, 1, 40, 70, false) == Event::None);
  assert(moved.update(2600, 0, 0, 0, false) == Event::None);

  // Jitter within tolerance is still a tap.
  Gesture jitter;
  jitter.update(0, 1, 40, 70, false);
  jitter.update(30, 1, 45, 74, false);
  assert(jitter.update(80, 0, 0, 0, false) == Event::Tap);

  // Busy starts before, during, or on release; none creates a deferred tap.
  for (int busyPhase = 0; busyPhase < 3; ++busyPhase) {
    Gesture busy;
    busy.update(0, 1, 40, 70, busyPhase == 0);
    busy.update(1000, 1, 40, 70, busyPhase == 1);
    assert(busy.update(1100, 0, 0, 0, busyPhase == 2) == Event::None);
    assert(busy.update(1200, 0, 0, 0, false) == Event::None);
  }

  Gesture multi;
  multi.update(0, 1, 40, 70, false);
  multi.update(100, 2, 40, 70, false);
  assert(!multi.canFlick());
  assert(multi.update(2200, 1, 40, 70, false) == Event::None);
  assert(multi.update(2300, 0, 0, 0, false) == Event::None);

  Gesture flick;
  flick.update(0, 1, 40, 70, false);
  flick.cancel();
  assert(!flick.canFlick());
  assert(flick.update(100, 0, 0, 0, false) == Event::None);

  // The gap/border/multi-touch keep the menu open. Dragging a rejected contact
  // onto a button does not select; lift all fingers then touch a choice anew.
  Menu menu;
  assert(menu.contact(1, 160, 64) == Action::None);
  for (int x : {0, 160}) {
    menu.open();
    menu.contact(0, 0, 0);
    assert(menu.contact(1, x, 120) == Action::None);
    assert(menu.contact(1, 160, 64) == Action::None);
    assert(menu.isOpen());
    menu.contact(0, 0, 0);
    assert(menu.contact(2, 160, 64) == Action::None);
    assert(menu.contact(1, 160, 64) == Action::None);
    assert(menu.isOpen());
    menu.contact(0, 0, 0);
    assert(menu.contact(1, 160, 64) == Action::SelectTouch);
    assert(!menu.isOpen());
    assert(menu.contact(0, 0, 0) == Action::None);
  }
  menu.open();
  menu.close();  // activity or app change
  assert(menu.contact(0, 0, 0) == Action::None);
  assert(menu.contact(1, 160, 64) == Action::None);
}

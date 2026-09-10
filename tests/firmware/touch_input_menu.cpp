#include <cassert>
#include <cstdint>
#include <initializer_list>
#include "share/TouchGesture.h"
#include "share/InputModeMenu.h"

using Gesture = stackchan::TouchGesture;
using Event = Gesture::Event;
using Menu = stackchan::InputModeMenu;
using Action = Menu::Action;

int main() {
  // Normal tap and the exact hold threshold (including millis rollover).
  for (uint32_t start : {uint32_t(100), UINT32_MAX - 1000}) {
    Gesture gesture;
    assert(gesture.update(start, 1, 30, 70, false) == Event::None);
    assert(gesture.update(start + 1999, 1, 30, 70, false) == Event::None);
    assert(gesture.update(start + 2000, 1, 30, 70, false) == Event::LongPress);
    Menu menu;
    menu.open();
    gesture.consume();
    assert(!gesture.canFlick());
    assert(gesture.update(start + 2100, 1, 200, 70, false) == Event::None);
    assert(gesture.update(start + 2200, 0, 0, 0, false) == Event::None);
    assert(menu.isOpen());  // Releasing the opening finger does NOT select Touch.
    assert(gesture.update(start + 2300, 1, 30, 70, false) == Event::None);
    assert(gesture.update(start + 2350, 0, 0, 0, false) == Event::Tap);
    assert(menu.tap(gesture.startX(), gesture.startY()) == Action::SelectTouch);
    assert(!menu.isOpen());
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

  // Selecting a mode closes the menu. All other
  // touches inside and outside the panel close only, not start conversation.
  Menu menu;
  assert(menu.tap(30, 70) == Action::None);
  menu.open();
  assert(menu.tap(30, 130) == Action::SelectAlways);
  assert(!menu.isOpen());
  assert(menu.tap(30, 130) == Action::None);
  menu.open();
  assert(menu.tap(10, 130) == Action::Close);
  assert(!menu.isOpen());
  menu.open();
  assert(menu.tap(30, 110) == Action::Close);  // gap, no invisible action
  menu.open();
  menu.close();  // modal flick, activity or app change
  assert(menu.tap(30, 70) == Action::None);
}

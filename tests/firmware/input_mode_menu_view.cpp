#include <cassert>
#include "mod/AiStackChan/InputModeMenuView.h"

int main() {
  M5Canvas canvas;
  InputModeMenuView view;
  view.draw(&canvas, m5avatar::BoundingRect{}, nullptr);
  assert(canvas.font == &fonts::lgfxJapanGothic_40);
  assert(canvas.drawnDatums.size() == 2);
  for (const auto datum : canvas.drawnDatums) {
    assert(datum == lgfx::textdatum_t::middle_center);
    assert(datum != lgfx::textdatum_t::middle_left);
  }
}

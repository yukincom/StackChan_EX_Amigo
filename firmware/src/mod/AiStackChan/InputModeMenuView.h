#pragma once

#include <Drawable.h>
#include "share/InputModeMenu.h"

class InputModeMenuView final : public m5avatar::Drawable {
 public:
  void draw(M5Canvas* canvas, m5avatar::BoundingRect,
            m5avatar::DrawContext*) override {
    canvas->setFont(&fonts::lgfxJapanGothic_40);
    canvas->setTextDatum(lgfx::textdatum_t::middle_center);
    stackchan::InputModeMenu::drawButtons(*canvas);
  }
};

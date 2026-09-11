#pragma once

// Use the installed production M5GFX enum, not a duplicate numeric fixture.
#include <lgfx/v1/misc/enum.hpp>
#include <vector>

namespace fonts {
static const int lgfxJapanGothic_40 = 40;
}

class M5Canvas {
 public:
  const int* font = nullptr;
  lgfx::textdatum_t datum = lgfx::textdatum_t::top_left;
  std::vector<lgfx::textdatum_t> drawnDatums;
  void setFont(const int* value) { font = value; }
  void setTextDatum(lgfx::textdatum_t value) { datum = value; }
  void fillScreen(int) {}
  void setTextColor(int, int) {}
  void setTextSize(int) {}
  void fillRect(int, int, int, int, int) {}
  void drawString(const char*, int, int) { drawnDatums.push_back(datum); }
};

namespace m5avatar {
struct BoundingRect {};
struct DrawContext {};
class Drawable {
 public:
  virtual ~Drawable() = default;
  virtual void draw(M5Canvas*, BoundingRect, DrawContext*) = 0;
};
}

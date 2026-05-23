// Copyright (c) Shinya Ishikawa. All rights reserved.
// Licensed under the MIT license. See LICENSE file in the project root for full
// license information.

#include "Eye.h"
#include <math.h>

namespace m5avatar {

static void drawConfusionSpiral(M5Canvas *spi, int32_t cx, int32_t cy, uint16_t baseR, bool isLeft, uint16_t color) {
  constexpr float turns = 1.7f;
  constexpr float innerRatio = 0.28f;
  constexpr float outerRatio = 1.48f;
  constexpr float lineWidth = 3.2f;
  constexpr float startAngle = -90.0f * 3.14159265f / 180.0f;
  constexpr float phaseOffset = 52.0f * 3.14159265f / 180.0f;
  const bool reverse = !isLeft;
  const float direction = reverse ? -1.0f : 1.0f;
  const float phase = reverse ? phaseOffset : 0.0f;
  const float inner = baseR * innerRatio;
  const float outer = baseR * outerRatio;
  const float maxTheta = 2.0f * 3.14159265f * turns;
  const int steps = 160;

  for (int i = 1; i <= steps; ++i) {
    float t0 = float(i - 1) / float(steps);
    float t1 = float(i) / float(steps);
    float th0 = maxTheta * t0;
    float th1 = maxTheta * t1;
    float a0 = startAngle + phase + direction * th0;
    float a1 = startAngle + phase + direction * th1;
    float r0 = inner + (outer - inner) * t0;
    float r1 = inner + (outer - inner) * t1;
    int x0 = int(cx + cosf(a0) * r0);
    int y0 = int(cy + sinf(a0) * r0);
    int x1 = int(cx + cosf(a1) * r1);
    int y1 = int(cy + sinf(a1) * r1);
    spi->drawLine(x0, y0, x1, y1, color);
    if (lineWidth > 2.0f) {
      spi->drawLine(x0 + 1, y0, x1 + 1, y1, color);
      spi->drawLine(x0, y0 + 1, x1, y1 + 1, color);
    }
  }
}

Eye::Eye(uint16_t x, uint16_t y, uint16_t r, bool isLeft) : Eye(r, isLeft) {}

Eye::Eye(uint16_t r, bool isLeft) : r{r}, isLeft{isLeft} {}

void Eye::draw(M5Canvas *spi, BoundingRect rect, DrawContext *ctx) {
  Expression exp = ctx->getExpression();
  uint32_t x = rect.getCenterX();
  uint32_t y = rect.getCenterY();
  Gaze g = ctx->getGaze();
  float openRatio = ctx->getEyeOpenRatio();
  uint32_t offsetX = g.getHorizontal() * 3;
  uint32_t offsetY = g.getVertical() * 3;
  uint16_t primaryColor = ctx->getColorDepth() == 1 ? 1 : ctx->getColorPalette()->get(COLOR_PRIMARY);
  uint16_t backgroundColor = ctx->getColorDepth() == 1 ? 0 : ctx->getColorPalette()->get(COLOR_BACKGROUND);

  if (openRatio > 0) {
    if (exp == Expression::Confusion) {
      drawConfusionSpiral(spi, x + offsetX, y + offsetY, r + 2, isLeft, primaryColor);
      return;
    }
    spi->fillCircle(x + offsetX, y + offsetY, r, primaryColor);
    // TODO(meganetaaan): Refactor
    if (exp == Expression::Angry || exp == Expression::Sad) {
      int x0, y0, x1, y1, x2, y2;
      x0 = x + offsetX - r;
      y0 = y + offsetY - r;
      x1 = x0 + r * 2;
      y1 = y0;
      x2 = !isLeft != !(exp == Expression::Sad) ? x0 : x1;
      y2 = y0 + r;
      spi->fillTriangle(x0, y0, x1, y1, x2, y2, backgroundColor);
    }
    if (exp == Expression::Happy || exp == Expression::Sleepy) {
      int x0, y0, w, h;
      x0 = x + offsetX - r;
      y0 = y + offsetY - r;
      w = r * 2 + 4;
      h = r + 2;
      if (exp == Expression::Happy) {
        y0 += r;
        spi->fillCircle(x + offsetX, y + offsetY, r / 1.5, backgroundColor);
      }
      spi->fillRect(x0, y0, w, h, backgroundColor);
    }
  } else {
    int x1 = x - r + offsetX;
    int y1 = y - 2 + offsetY;
    int w = r * 2;
    int h = 4;
    spi->fillRect(x1, y1, w, h, primaryColor);
  }
}
}  // namespace m5avatar

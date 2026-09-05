#include "IdleExpression.h"
#include "IdleExpressionPolicy.h"

#include <Arduino.h>
#include <Avatar.h>
#include <time.h>

extern m5avatar::Avatar avatar;

namespace {
stackchan::IdleExpressionPolicy policy;

bool readHour(int& hour) {
  struct tm now = {};
  // getLocalTime defaults to a multi-second wait; periodic face updates must
  // not block the main loop while NTP is unavailable.
  const bool ready = getLocalTime(&now, 0);
  hour = now.tm_hour;
  return ready;
}
}  // namespace

void setIdleExpression() {
  int hour;
  const bool ready = readHour(hour);
  avatar.setExpression(policy.idle(ready, hour));
}

void setActivityExpression(m5avatar::Expression expression) {
  policy.activity(expression);
  avatar.setExpression(expression);
}

void setExplicitExpression(m5avatar::Expression expression) {
  policy.explicitExpression(expression);
  avatar.setExpression(expression);
}

void refreshIdleExpression(bool busy) {
  static uint32_t lastCheck = 0;
  const uint32_t now = millis();
  if (busy || static_cast<uint32_t>(now - lastCheck) < 1000) return;
  lastCheck = now;
  int hour;
  const bool ready = readHour(hour);
  if (policy.refresh(ready, hour, busy, avatar.getExpression())) {
    avatar.setExpression(policy.expression());
  }
}

void finishExpressionActivity(m5avatar::Expression expression) {
  if (policy.ownsActivity(expression) && avatar.getExpression() == expression) {
    setIdleExpression();
  }
}

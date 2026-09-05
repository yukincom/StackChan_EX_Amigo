#pragma once

#include <Expression.h>

namespace stackchan {

// Local hour is supplied by the existing JST clock. No clock/device I/O here.
inline m5avatar::Expression idleExpressionForHour(bool clockReady, int hour) {
  return clockReady && hour >= 0 && hour < 24 && (hour >= 19 || hour < 7)
      ? m5avatar::Expression::Sleepy : m5avatar::Expression::Neutral;
}

class IdleExpressionPolicy {
 public:
  enum class Owner { Idle, Activity, Explicit };

  m5avatar::Expression idle(bool clockReady, int hour) {
    owner_ = Owner::Idle;
    expression_ = idleExpressionForHour(clockReady, hour);
    return expression_;
  }

  void activity(m5avatar::Expression expression) {
    owner_ = Owner::Activity;
    expression_ = expression;
  }

  void explicitExpression(m5avatar::Expression expression) {
    owner_ = Owner::Explicit;
    expression_ = expression;
  }

  bool refresh(bool clockReady, int hour, bool busy,
               m5avatar::Expression visible) {
    // A legacy feature may own the avatar without using this policy. Never
    // replace a face which is no longer the idle face we last applied.
    if (busy || owner_ != Owner::Idle || visible != expression_) return false;
    const auto next = idleExpressionForHour(clockReady, hour);
    if (next == expression_) return false;
    expression_ = next;
    return true;
  }

  bool ownsActivity(m5avatar::Expression expression) const {
    return owner_ == Owner::Activity && expression_ == expression;
  }

  m5avatar::Expression expression() const { return expression_; }

 private:
  Owner owner_ = Owner::Idle;
  m5avatar::Expression expression_ = m5avatar::Expression::Neutral;
};

}  // namespace stackchan

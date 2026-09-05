#include <cassert>
#include "share/IdleExpressionPolicy.h"

int main() {
  using m5avatar::Expression;
  using stackchan::idleExpressionForHour;
  assert(idleExpressionForHour(true, 18) == Expression::Neutral);
  assert(idleExpressionForHour(true, 19) == Expression::Sleepy);
  assert(idleExpressionForHour(true, 0) == Expression::Sleepy);
  assert(idleExpressionForHour(true, 6) == Expression::Sleepy);
  assert(idleExpressionForHour(true, 7) == Expression::Neutral);
  assert(idleExpressionForHour(false, 23) == Expression::Neutral);
  stackchan::IdleExpressionPolicy policy;
  policy.idle(true, 18);
  assert(!policy.refresh(true, 19, true, Expression::Neutral));
  assert(policy.refresh(true, 19, false, Expression::Neutral));
  assert(policy.expression() == Expression::Sleepy);
  policy.activity(Expression::Doubt);
  assert(!policy.refresh(true, 7, false, Expression::Doubt));
  assert(policy.ownsActivity(Expression::Doubt));
  policy.explicitExpression(Expression::Sleepy);
  assert(!policy.refresh(true, 7, false, Expression::Sleepy));
  assert(!policy.ownsActivity(Expression::Sleepy));
  assert(policy.idle(true, 7) == Expression::Neutral);
  assert(!policy.refresh(true, 19, false, Expression::Happy));
}

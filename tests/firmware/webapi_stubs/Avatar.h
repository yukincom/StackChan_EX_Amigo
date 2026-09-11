#pragma once
#include <Expression.h>
namespace m5avatar {
class Avatar {
 public:
  Expression expression = Expression::Neutral;
  bool subWindow = false;
  void setExpression(Expression value) { expression = value; }
  Expression getExpression() const { return expression; }
  void set_isSubWindowEnable(bool enabled) { subWindow = enabled; }
};
}

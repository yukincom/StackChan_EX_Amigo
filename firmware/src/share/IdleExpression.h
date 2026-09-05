#pragma once

#include <Expression.h>

// Call only on the main task, like the existing avatar expression updates.
void setIdleExpression();
void setActivityExpression(m5avatar::Expression expression);
void setExplicitExpression(m5avatar::Expression expression);
void refreshIdleExpression(bool busy);
void finishExpressionActivity(m5avatar::Expression expression);

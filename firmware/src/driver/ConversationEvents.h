#pragma once

#include "share/ConversationSession.h"

namespace stackchan {
// Uses the same explicitly configured local-server base URL/key as chat.
class DeviceConversationTransport : public ConversationTransport {
 public:
  int post(const std::string& body, std::string& response) override;
};

ConversationSession& conversationSession();
}  // namespace stackchan

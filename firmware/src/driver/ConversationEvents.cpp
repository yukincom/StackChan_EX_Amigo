#include "ConversationEvents.h"
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <esp_system.h>
#include "Robot.h"
#include "share/HttpUtil.h"

namespace stackchan {
namespace {
std::string randomId() {
  char id[33];
  snprintf(id, sizeof(id), "%08lx%08lx%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
  return id;
}

class BoundedResponse : public Stream {
 public:
  explicit BoundedResponse(std::string& value) : value_(value) {}
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* data, size_t size) override {
    if (size > ConversationSession::MaxResponseBytes - value_.size()) return 0;
    value_.append(reinterpret_cast<const char*>(data), size);
    return size;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  void flush() override {}
 private:
  std::string& value_;
};
}  // namespace

int DeviceConversationTransport::post(const std::string& body, std::string& response) {
  response.clear();
  if (robot == nullptr || robot->llm == nullptr) return -1;
  const auto& config = robot->llm->param;
  if (config.llm_conf.base_url.isEmpty()) return -1;  // No public-API fallback.
  const String url = join_url(config.llm_conf.base_url, "/v1/conversation/events");
  WiFiClient plain;
  WiFiClientSecure secure;
  HTTPClient http;
  bool opened;
  if (is_https_url(url)) {
    secure.setInsecure();  // Same custom-server TLS policy as existing chat.
    opened = http.begin(secure, url);
  } else {
    opened = http.begin(plain, url);
  }
  if (!opened) return -1;
  http.setTimeout(65000);  // Match existing chat's generation timeout.
  http.addHeader("Content-Type", "application/json");
  if (!config.api_key.isEmpty()) http.addHeader("Authorization", String("Bearer ") + config.api_key);
  const int status = http.POST(reinterpret_cast<uint8_t*>(const_cast<char*>(body.data())), body.size());
  if (status > 0) {
    BoundedResponse output(response);
    if (http.getSize() > int(ConversationSession::MaxResponseBytes) || http.writeToStream(&output) < 0) {
      http.end();
      response.clear();
      return -1;
    }
  }
  http.end();
  return status;
}

ConversationSession& conversationSession() {
  static DeviceConversationTransport transport;
  static ConversationSession session(transport, randomId, 2);
  return session;
}

ConversationSession& touchActionSession() {
  static DeviceConversationTransport transport;
  static ConversationSession session(transport, randomId, 2);
  return session;
}

std::string newConversationId() { return randomId(); }
}  // namespace stackchan

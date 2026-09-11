#pragma once
#include <string>
#include "Arduino.h"
constexpr int HTTP_GET = 0;
constexpr int HTTP_POST = 1;
class ESP32WebServer {
 public:
  explicit ESP32WebServer(int) {}
  std::string requestBody;
  int requestMethod = HTTP_POST;
  int responseCode = 0;
  std::string responseBody;
  const char* arg(const char* name) const { return std::string(name) == "plain" ? requestBody.c_str() : ""; }
  const char* arg(unsigned) const { return ""; }
  String argName(unsigned) const { return ""; }
  unsigned args() const { return 0; }
  String uri() const { return "/stub"; }
  int method() const { return requestMethod; }
  void send(int code, const char*, String body) { responseCode = code; responseBody = body.c_str(); }
  template<class... T> void on(T...) {}
  template<class T> void onNotFound(T) {}
  void begin() {}
  void handleClient() {}
};

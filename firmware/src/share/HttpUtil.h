#ifndef _HTTP_UTIL_H_
#define _HTTP_UTIL_H_

#include <Arduino.h>

bool is_https_url(const String& url);
String join_url(const String& base_url, const char* path);

#endif  //_HTTP_UTIL_H_

#include "share/HttpUtil.h"

bool is_https_url(const String& url)
{
  return url.startsWith("https://");
}

String join_url(const String& base_url, const char* path)
{
  if (base_url.isEmpty()) {
    return String(path);
  }

  String url = base_url;
  String suffix = String(path);

  if (url.endsWith("/") && suffix.startsWith("/")) {
    url.remove(url.length() - 1);
  } else if (!url.endsWith("/") && !suffix.startsWith("/")) {
    url += "/";
  }

  url += suffix;
  return url;
}

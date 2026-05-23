#ifndef _WEB_API_H
#define _WEB_API_H

#include <Arduino.h>
#include <M5Unified.h>

extern void init_web_server(void);
extern void web_server_handle_client(void);
extern void process_play_request(void);
extern void process_camera_trigger_request(void);
extern void mark_camera_trigger_awaiting(uint32_t timeout_ms = 8000);
extern void clear_camera_trigger_awaiting(void);
extern bool is_camera_trigger_active(void);
extern bool request_camera_trigger(
  const String& requester,
  const String& context,
  const String& mode,
  const String& speaker,
  const String& speaker_label,
  const String& announce_endpoint = ""
);

#endif  //_WEB_API_H

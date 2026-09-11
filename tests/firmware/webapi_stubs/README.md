# WebAPI host boundaries

`webapi_actions.cpp` directly includes production `WebAPI.cpp` and uses the
production `ConversationSession` with the actual ArduinoJson dependency.
Queue/admission/timeout/ACK logic is not duplicated in a model.

The prelude suppresses heavy hardware headers using their include guards and
provides minimal compatible declarations. Device builds validate the actual
public signatures. Clock, HTTP ingress, Robot, Avatar, camera upload, MP3 calls
and error-audio helper are boundary stubs. The fake transport only supplies
fictional remote responses and records requests; it is not a second session.

These tests verify one-time execution, correlation, checked-playback selection,
failure fallback requests and ACK recovery. Real decoder coverage is in
`mp3_playback.cpp`. The SD cache, audible error helper, physical camera, I2S,
network and selected input mode in AiStackChan require their separate checks.

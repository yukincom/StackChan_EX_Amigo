#pragma once

#include <AudioFileSourceBuffer.h>
#include <AudioGeneratorMP3.h>
#include "AudioOutputM5Speaker.h"

namespace stackchan {

inline bool mp3BodyComplete(uint32_t size, uint32_t position) {
  return size > 0 && size != UINT32_MAX && position >= size;
}

// The existing synchronous decoder loop, shared by the device wrapper and
// real-codec host regressions. Device Mic/Speaker ownership stays in playMP3.
inline bool decodeMp3(AudioGeneratorMP3& decoder, AudioFileSourceBuffer& source,
                      AudioOutputM5Speaker& output, bool checkCompletion) {
  struct DecodeStatus {
    AudioOutputM5Speaker* output;
    bool failed;
  } status{&output, false};
  output.setDrainOnStop(checkCompletion);
  decoder.RegisterStatusCB([](void* context, int code, const char*) {
    auto& state = *static_cast<DecodeStatus*>(context);
    // libmad scans past leading ID3 metadata before finding the first frame.
    // Once audio has decoded, a sync loss is corruption, not a leading tag.
    if (code != MAD_ERROR_BUFLEN
        && !(code == MAD_ERROR_LOSTSYNC && !state.output->hasDecodedAudio())) {
      state.failed = true;
    }
  }, &status);

  if (!decoder.begin(&source, &output)) {
    decoder.RegisterStatusCB(nullptr, nullptr);
    output.setDrainOnStop(false);
    return false;
  }
  bool reachedEof = false;
  while (decoder.isRunning()) {
    if (!decoder.loop()) {
      // Natural EOF returns false with running still set. A decoder failure
      // stops internally or reports a status; neither is played-complete.
      reachedEof = decoder.isRunning();
      decoder.stop();
    }
    delay(1);
  }
  decoder.RegisterStatusCB(nullptr, nullptr);
  output.setDrainOnStop(false);
  return !checkCompletion || (reachedEof && !status.failed
      && output.hasDecodedAudio() && output.playbackDelivered());
}

}  // namespace stackchan

#ifndef _Whisper_H
#define _Whisper_H
#include "STTBase.h"
#include "driver/AudioWhisper.h"

class Whisper : public STTBase {
private:
  String recognize(bool cancelOnTouch);
public:
  Whisper(stt_param_t param);
  ~Whisper();
  String Transcribe(AudioWhisper* audio);
  String speech_to_text() override;
  String speech_to_text_interruptible() override;
  bool supportsInterruptible() const override { return true; }
};

#endif // _Whisper_H

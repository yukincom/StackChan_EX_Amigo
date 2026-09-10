#pragma once

#include "TTSBase.h"

class OpenAITTS: public TTSBase{
private:
    bool streamImpl(String text, bool checkCompletion);

public:
    OpenAITTS(tts_param_t param) : TTSBase(param) {};
    void stream(String text);
    bool streamChecked(String text) override;
    bool supportsCheckedStream() const override { return true; }
};

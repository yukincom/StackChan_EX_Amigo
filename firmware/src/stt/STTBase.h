#ifndef _STT_BASE_H
#define _STT_BASE_H

#include <Arduino.h>
#include "StackchanExConfig.h"

struct stt_param_t
{
  String api_key;
  stt_s stt_conf;
};

enum class STTErrorType : uint8_t {
  None = 0,
  NoSpeech,
  Connection,
  Http,
  Parse,
};

class STTBase{
protected:
    stt_param_t param;
    STTErrorType lastError = STTErrorType::None;

public:
    bool isOfflineService;
    
    STTBase() {};
    STTBase(stt_param_t param) : param{param}, isOfflineService{false} {};
    virtual String speech_to_text() = 0;
    STTErrorType getLastError() const { return lastError; }
    void clearLastError() { lastError = STTErrorType::None; }

};



#endif //_STT_BASE_H

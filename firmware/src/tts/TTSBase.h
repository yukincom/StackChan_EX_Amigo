#ifndef _TTS_BASE_H
#define _TTS_BASE_H

#include <Arduino.h>
#include "driver/PlayMP3.h"

struct tts_param_t
{
  String api_key;
  String model;
  String voice;
  String base_url;

};


class TTSBase{
protected:
    tts_param_t param;

public:
    bool isOfflineService;
    
    TTSBase() {};
    TTSBase(tts_param_t param) : param(param), isOfflineService(false) {};
    virtual void stream(String text) = 0;
    // Opt-in acknowledgement support. Unknown backends cannot claim success.
    virtual bool streamChecked(String text) { return false; };
    virtual bool supportsCheckedStream() const { return false; }
    virtual int getLevel(){ return out.getLevel(); };

};



#endif //_TTS_BASE_H

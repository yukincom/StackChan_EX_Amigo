#ifndef _AI_STACKCHAN_MOD_H
#define _AI_STACKCHAN_MOD_H

#include <Arduino.h>
#include "mod/ModBase.h"

class AiStackChanMod: public ModBase{
private:
    box_t box_servo;
    box_t box_stt;
    box_t box_BtnA;
    box_t box_BtnC;
    box_t box_wakeword_toggle;
    box_t box_wakeword_register;
    #if defined(ENABLE_CAMERA)
    box_t box_subWindow;
    #endif
    String avatarText;
    bool isOffline;
    unsigned long textWakewordNextCheckMs = 0;
    unsigned long startupSpeechClearAtMs = 0;
    void clear_all_wakewords(void);
    void set_wakeword_enabled(bool enabled);
    void toggle_wakeword_enabled(void);
    bool handle_text_wakeword(void);
public:
    AiStackChanMod(bool _isOffline);

    void init(void);
    void pause(void);
    void update(int page_no);
    void btnA_pressed(void);
    void btnB_longPressed(void);
    void btnC_pressed(void);
    void display_touched(int16_t x, int16_t y);
    bool display_long_touched(int16_t x, int16_t y);
    void doubleTapped(float ax, float ay, float az);   // 加速度センサによるダブルタップ検出のコールバック。platformio.iniで-DENABLE_TAP_DETECTを有効にしてください
    void idle(void);
};


#endif  //_AI_STACKCHAN_MOD_H

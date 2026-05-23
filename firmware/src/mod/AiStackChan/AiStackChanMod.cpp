#include <Arduino.h>
#include <deque>
#include <SD.h>
#include <SPIFFS.h>
#include "mod/ModManager.h"
#include "AiStackChanMod.h"
#include <Avatar.h>
#include "Robot.h"
#include "llm/ChatGPT/ChatGPT.h"
#include "llm/ChatGPT/FunctionCall.h"
#include "driver/PlayMP3.h"
#include "driver/WakeWord.h"
#include <WiFiClientSecure.h>
#include "Scheduler.h"
#include "MySchedule.h"
#include "share/SDUtil.h"
#include "WebAPI.h"
#if defined( ENABLE_CAMERA )
#include "driver/Camera.h"
#endif
using namespace m5avatar;

#if defined(ENABLE_WAKEWORD)
bool wakeword_is_enable = false;
#endif

/// 外部参照 ///
extern Avatar avatar;
extern bool servo_home;
extern volatile bool g_cameraBusy;
//extern bool wakeword_is_enable;
extern void sw_tone();
extern void alarm_tone();
///////////////

static bool is_stt_backend_error() {
  if (robot == nullptr || robot->stt == nullptr) {
    return false;
  }
  auto error = robot->stt->getLastError();
  return error == STTErrorType::Connection
      || error == STTErrorType::Http
      || error == STTErrorType::Parse;
}

static void restore_post_chat_expression() {
  if (is_camera_trigger_active()) {
    avatar.setExpression(Expression::Doubt);
  } else {
    avatar.setExpression(Expression::Neutral);
  }
}

static void report_batt_level(){
  char buff[100];
  if (g_cameraBusy) {
    avatar.setExpression(Expression::Doubt);
    robot->speech(String("いまカメラを使っているので、あとで電池を確認するね。"));
    delay(1000);
    avatar.setExpression(Expression::Neutral);
    return;
  }
  int level = M5.Power.getBatteryLevel();
#if defined(ENABLE_WAKEWORD)
  mode = 0;
#endif
  if(M5.Power.isCharging())
    sprintf(buff,"充電中、バッテリーのレベルは%d％です。",level);
  else
    sprintf(buff,"バッテリーのレベルは%d％です。",level);
  avatar.setExpression(Expression::Happy);
#if defined(ENABLE_WAKEWORD)
  mode = 0; 
#endif
  robot->speech(String(buff));
  delay(1000);
  avatar.setExpression(Expression::Neutral);
}


static void STT_ChatGPT(const char *base64_buf = NULL) {
  bool prev_servo_home = servo_home;
#ifdef USE_SERVO
  servo_home = true;
#endif

  avatar.setExpression(Expression::Happy);

  String ret = robot->listen();

#ifdef USE_SERVO
  //servo_home = prev_servo_home;
  servo_home = false;
#endif
  Serial.println("音声認識終了");
  Serial.println("音声認識結果");
  if(ret != "") {
    Serial.println(ret);
    robot->chat(ret, base64_buf);
    restore_post_chat_expression();
    servo_home = true;
  } else {
    Serial.println("音声認識失敗");
    if (is_stt_backend_error()) {
      avatar.setExpression(Expression::Confusion);
      playLocalPrompt("server_error");
    } else {
      avatar.setExpression(Expression::Sad);
      avatar.setSpeechText("聞き取れませんでした");
      playLocalPrompt("listening_error");
    }
    delay(2000);
    avatar.setSpeechText("");
    avatar.setExpression(Expression::Neutral);
    servo_home = true;
  } 
}



AiStackChanMod::AiStackChanMod(bool _isOffline)
  : isOffline{_isOffline}
{
  box_servo.setupBox(80, 120, 80, 80);
  // タップ起動を優先し、画面全体を会話開始領域として扱う。
  // モード切替はフリックのまま残す。
  box_stt.setupBox(0, 0, M5.Display.width(), M5.Display.height());
#if defined(ENABLE_CAMERA)
  box_subWindow.setupBox(0, 0, 0, 0);
#endif
  box_BtnA.setupBox(0, 0, 0, 0);
  box_BtnC.setupBox(0, 0, 0, 0);
  box_wakeword_toggle.setupBox(0, 0, 0, 0);
  box_wakeword_register.setupBox(0, 0, 0, 0);

  //SDカードのMP3ファイル（アラーム用）をSPIFFSにコピーする（SDカードだと音が途切れ途切れになるため）。
  //すでにSPIFFSにファイルがあればコピーはしない。強制的にコピー（上書き）したい場合は第2引数をtrueにする。
  //String fname = String(APP_DATA_PATH) + String(FNAME_ALARM_MP3);
  //copySDFileToSPIFFS(fname.c_str(), false);

  if(!isOffline){
    //スケジューラ設定
    init_schedule();
  }


  int wakeword_type = robot->m_config.getExConfig().wakeword.type;
#if defined(ENABLE_WAKEWORD)
  if (wakeword_type == WAKEWORD_TYPE_TEXT_TRIGGER) {
    wakeword_is_enable = false;
    mode = 0;
    if (robot->m_config.getExConfig().wakeword.keyword == "") {
      Serial.println("Text wakeword keyword is empty");
    } else {
      Serial.printf("Text wakeword enabled: %s\n", robot->m_config.getExConfig().wakeword.keyword.c_str());
    }
  }
  else if (wakeword_type == WAKEWORD_TYPE_SIMPLEVOX) {
    wakeword_init();
    if (wakeword_registered_count() > 0) {
      mode = 1;
      wakeword_is_enable = true;
      Serial.printf("Wakeword auto-enabled with %d entries\n", wakeword_registered_count());
    } else {
      mode = 0;
      wakeword_is_enable = false;
      Serial.println("Wakeword entries not found");
    }
  }
  else{
    wakeword_is_enable = false;
    mode = 0;
    Serial.printf("Unsupported wakeword type: %d\n", wakeword_type);
  }
#endif

}


void AiStackChanMod::init(void)
{
  avatar.setSpeechText("AI Stack-chan");
  startupSpeechClearAtMs = millis() + 2000;
#if defined(ENABLE_CAMERA)
  if(isSubWindowON){
    avatar.set_isSubWindowEnable(true);
  }
#endif
}

void AiStackChanMod::pause(void)
{
#if defined(ENABLE_CAMERA)
  if(isSubWindowON){
    avatar.set_isSubWindowEnable(false);
  }
#endif
}


void AiStackChanMod::update(int page_no)
{
}

void AiStackChanMod::set_wakeword_enabled(bool enabled)
{
#if defined(ENABLE_WAKEWORD)
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  if (mode < 0) {
    return;
  }

  sw_tone();
  if (enabled) {
    avatar.setSpeechText("ウェイクワードを有効にしたよ");
    mode = 1;
    wakeword_is_enable = true;
    playLocalPrompt("wakeword_enable");
  } else {
    avatar.setSpeechText("ウェイクワードを無効にしたよ");
    mode = 0;
    wakeword_is_enable = false;
  }
  delay(1000);
  avatar.setSpeechText("");
#endif  //ENABLE_WAKEWORD
}

void AiStackChanMod::toggle_wakeword_enabled(void)
{
#if defined(ENABLE_WAKEWORD)
  set_wakeword_enabled(mode == 0);
#endif  //ENABLE_WAKEWORD
}

void AiStackChanMod::btnA_pressed(void)
{
#if defined(ARDUINO_M5STACK_ATOMS3R)
  sw_tone();
  STT_ChatGPT();
#else

#if defined(ENABLE_WAKEWORD)
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  if(mode >= 0){
    toggle_wakeword_enabled();
  }
#endif  //ENABLE_WAKEWORD

#endif  //ARDUINO_M5STACK_ATOMS3R
}


void AiStackChanMod::btnB_longPressed(void)
{
#if defined(ENABLE_WAKEWORD)
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  M5.Mic.end();
  M5.Speaker.tone(1000, 100);
  delay(500);
  M5.Speaker.tone(600, 100);
  delay(1000);
  M5.Speaker.end();
  M5.Mic.begin();
  wakeword_is_enable = false; //wakeword 無効
  mode = -1;
#ifdef USE_SERVO
    servo_home = true;
    delay(500);
#endif
  avatar.setSpeechText("合図の後に録音するよ");
#endif
}

void AiStackChanMod::btnC_pressed(void)
{
  static bool isQrDrawing = false;
  if(!isQrDrawing){
    avatar.setSpeechText("");
    String url = String("http://") + WiFi.localIP().toString();
    avatar.updateSubWindowQrcode(url);
    avatar.set_isSubWindowEnable(true);
    isQrDrawing = true;
  }else{
    avatar.set_isSubWindowEnable(false);
    isQrDrawing = false;
  }
}

void AiStackChanMod::clear_all_wakewords(void)
{
#if defined(ENABLE_WAKEWORD)
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  SPIFFS.begin(true);
  int deleted_count = 0;

  for (int i = 0; i < REGISTER_MAX; i++)
  {
    String filename = filename_base + String(i) + String(".bin");
    if (SPIFFS.exists(filename.c_str()))
    {
      SPIFFS.remove(filename.c_str());
      deleted_count++;
    }
    delete_mfcc(i);
  }

  mode = 0;
  wakeword_is_enable = false;

  String text = deleted_count > 0
    ? String("ウェイクワード") + String(deleted_count) + String("件削除")
    : String("ウェイクワードなし");
  Serial.println(text);
  avatar.setSpeechText(text.c_str());
  delay(1200);
  avatar.setSpeechText("");
#endif
}

bool AiStackChanMod::handle_text_wakeword(void)
{
  String transcript = robot->listen();
  if (transcript == "") {
    return false;
  }

  Serial.println("テキスト起動判定");
  Serial.println(transcript);

  String normalized = transcript;
  normalized.trim();

  String keywords = robot->m_config.getExConfig().wakeword.keyword;
  keywords.replace("、", ",");
  keywords.replace("|", ",");

  bool matched = false;
  String query = normalized;
  int start = 0;
  while (start <= keywords.length())
  {
    int comma = keywords.indexOf(',', start);
    String keyword = comma >= 0 ? keywords.substring(start, comma) : keywords.substring(start);
    keyword.trim();
    if (keyword != "" && normalized.indexOf(keyword) >= 0)
    {
      matched = true;
      query = normalized;
      query.replace(keyword, "");
      query.trim();
      break;
    }
    if (comma < 0) {
      break;
    }
    start = comma + 1;
  }

  if (!matched) {
    return false;
  }

  while (query.length() > 0)
  {
    if (query.startsWith("、")) {
      query.remove(0, String("、").length());
    }
    else if (query.startsWith("。")) {
      query.remove(0, String("。").length());
    }
    else if (query.startsWith("？")) {
      query.remove(0, String("？").length());
    }
    else if (query.startsWith("！")) {
      query.remove(0, String("！").length());
    }
    else if (query.startsWith("　")) {
      query.remove(0, String("　").length());
    }
    else if (query.startsWith("?") || query.startsWith("!") || query.startsWith(" ")) {
      query.remove(0, 1);
    }
    else {
      break;
    }
    query.trim();
  }

  textWakewordNextCheckMs = millis() + 1500;
  sw_tone();

  if (query == "")
  {
#ifdef USE_SERVO
    servo_home = true;
#endif
    avatar.setExpression(Expression::Happy);
    STT_ChatGPT();
    return true;
  }

#ifdef USE_SERVO
  servo_home = true;
#endif
  avatar.setExpression(Expression::Happy);
  robot->chat(query);
  avatar.setSpeechText("");
  restore_post_chat_expression();
  servo_home = true;
  return true;
}

void AiStackChanMod::display_touched(int16_t x, int16_t y)
{
  if (!box_stt.contain(x, y))
  {
    return;
  }

  if (is_camera_trigger_active()) {
    return;
  }

  sw_tone();
#if defined(ENABLE_CAMERA)
  avatar.set_isSubWindowEnable(false);
  if(isSubWindowON){
    String base64;
    bool ret = camera_capture_base64(base64);
    STT_ChatGPT(base64.c_str());
  }
  else{
    STT_ChatGPT();
  }
  avatar.set_isSubWindowEnable(isSubWindowON);
#else
  STT_ChatGPT();
#endif
}

void AiStackChanMod::doubleTapped(float ax, float ay, float az)
{
  Serial.printf("Mod double tapped. ax=%.3f ay=%.3f az=%.3f\n", ax, ay, az);
  if (is_camera_trigger_active()) {
    return;
  }
#if defined(ARDUINO_M5STACK_ATOMS3R)
  sw_tone();
  STT_ChatGPT();
#endif
}

void AiStackChanMod::idle(void)
{
  if (startupSpeechClearAtMs != 0 && millis() >= startupSpeechClearAtMs) {
    avatar.setSpeechText("");
    startupSpeechClearAtMs = 0;
  }

  /// Face detect ///
#if defined(ENABLE_CAMERA)
  bool shouldHandleCamera = isSubWindowON;
#if defined(ENABLE_FACE_DETECT)
  shouldHandleCamera = true;
#endif
  if (shouldHandleCamera) {
    //顔が検出されれば音声認識を開始。
    bool isFaceDetected;
    isFaceDetected = camera_capture_and_face_detect();
    if(!isSilentMode){

#if defined(ENABLE_FACE_DETECT)
      if(isFaceDetected){
        avatar.set_isSubWindowEnable(false);
        sw_tone();
        STT_ChatGPT();                              //音声認識

        // フレームバッファを読み捨てる（ｽﾀｯｸﾁｬﾝが応答した後に、過去のフレームで顔検出してしまうのを防ぐため）
        M5.In_I2C.release();
        camera_fb_t *fb = esp_camera_fb_get();
        esp_camera_fb_return(fb);
        avatar.set_isSubWindowEnable(isSubWindowON);
      }
#endif
    }
    else{
#if defined(ENABLE_FACE_DETECT)
      if(isFaceDetected){
        avatar.setExpression(Expression::Happy);
        //delay(2000);
        //avatar.setExpression(Expression::Neutral);
      }
      else{
        avatar.setExpression(Expression::Neutral);
      }
#endif
    }
  }
#endif  //ENABLE_CAMERA

  //Wakeword
#if defined(ENABLE_WAKEWORD)
    if (robot->m_config.getExConfig().wakeword.type == WAKEWORD_TYPE_TEXT_TRIGGER) {
      if (robot->m_config.getExConfig().wakeword.keyword != "" && millis() >= textWakewordNextCheckMs) {
        textWakewordNextCheckMs = millis() + 500;
        handle_text_wakeword();
      }
    }
    else if (mode == 0) { /* return; */ }
    else if (mode < 0) {
      int idx = wakeword_regist();
      if(idx >= 0){
        String text = String("no.") + String(idx) + String("を登録したよ！");
        avatar.setSpeechText(text.c_str());
        delay(1000);
        avatar.setSpeechText("");
        //mode = 0;
        //wakeword_is_enable = false;
        mode = 1;
        wakeword_is_enable = true;
      }
      else if (idx == WAKEWORD_REGIST_NO_SLOT) {
        avatar.setSpeechText("空きがないよ");
        delay(1000);
        avatar.setSpeechText("");
        if (wakeword_registered_count() > 0) {
          mode = 1;
          wakeword_is_enable = true;
        } else {
          mode = 0;
          wakeword_is_enable = false;
        }
      }
    }
    else if (mode > 0 && wakeword_is_enable) {
      int idx = wakeword_compare();
      if( idx >= 0){
        Serial.println("wakeword_compare OK!");
        String text = String("ウェイクワード#") + String(idx);
        avatar.setSpeechText(text.c_str());
        sw_tone();
        STT_ChatGPT();
      }
    }

#if defined(ARDUINO_M5STACK_CORES3)
    // Function Callからの要求でウェイクワード有効化
    if (wakeword_enable_required)
    {
      wakeword_enable_required = false;
      set_wakeword_enabled(true);
    }

    // Function Callからの要求でウェイクワード無効化
    if (wakeword_disable_required)
    {
      wakeword_disable_required = false;
      set_wakeword_enabled(false);
    }

    // Function Callからの要求でウェイクワード登録
    if(register_wakeword_required)
    {
      register_wakeword_required = false;
      btnB_longPressed();
    }
#endif  //defined(ARDUINO_M5STACK_CORES3)
#endif  //ENABLE_WAKEWORD

  /// Alarm ///
  if(xAlarmTimer != NULL){
    TickType_t xRemainingTime;

    /* Query the period of the timer that expires. */
    xRemainingTime = xTimerGetExpiryTime( xAlarmTimer ) - xTaskGetTickCount();
    avatarText = "残り" + String(xRemainingTime / 1000) + "秒";
    avatar.setSpeechText(avatarText.c_str());
  }

  if (alarmTimerCallbacked) {
    alarmTimerCallbacked = false;
    avatar.setSpeechText("");
#if defined(ENABLE_CAMERA)
    avatar.set_isSubWindowEnable(false);
#endif    
    if(!SD.begin(GPIO_NUM_4, SPI, 25000000)) {
    //if(!SPIFFS.begin(true)){
      Serial.println("Failed to mount SD card. Use alarm tone.");
      alarm_tone();
    }
    else{
      String fname = String(APP_DATA_PATH) + String(FNAME_ALARM_MP3);
      bool result = playMP3SD(fname.c_str());
      if(!result){
        alarm_tone();
      }
    }
#if defined(ENABLE_CAMERA)
    avatar.set_isSubWindowEnable(isSubWindowON);
#endif  
  }

  //スケジューラ処理
  if(!isOffline){
    run_schedule();
  }

}

bool AiStackChanMod::display_long_touched(int16_t x, int16_t y)
{
  return false;
}

#include <Arduino.h>
#include <deque>
#include <SD.h>
#include <SPIFFS.h>
#include "mod/ModManager.h"
#include "AiStackChanMod.h"
#include "InputModeMenuView.h"
#include <Avatar.h>
#include "Robot.h"
#include "share/IdleExpression.h"
#include "llm/ChatGPT/ChatGPT.h"
#include "llm/ChatGPT/FunctionCall.h"
#include "driver/PlayMP3.h"
#include "driver/ConversationEvents.h"
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
      || error == STTErrorType::Parse
      || error == STTErrorType::Memory;
}

static void restore_post_chat_expression() {
  if (is_camera_trigger_active()) {
    setActivityExpression(Expression::Doubt);
  } else {
    setIdleExpression();
  }
}

static void report_batt_level(){
  char buff[100];
  if (g_cameraBusy) {
    setActivityExpression(Expression::Doubt);
    robot->speech(String("いまカメラを使っているので、あとで電池を確認するね。"));
    delay(1000);
    setIdleExpression();
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
  setActivityExpression(Expression::Happy);
#if defined(ENABLE_WAKEWORD)
  mode = 0; 
#endif
  robot->speech(String(buff));
  delay(1000);
  setIdleExpression();
}


static void STT_ChatGPT(const char *base64_buf = NULL, bool deviceActions = false) {
  if (deviceActions) {
    auto& session = stackchan::touchActionSession();
    if (session.pendingRetry() || is_camera_trigger_active()) return;
    if (session.phase() == stackchan::ConversationSession::Phase::Fault) {
      cancel_conversation_action();  // Explicit new Touch input, including Atom.
      session.abandon();
    }
  }
  bool prev_servo_home = servo_home;
#ifdef USE_SERVO
  servo_home = true;
#endif

  setActivityExpression(Expression::Happy);

  String ret = robot->listen();

#ifdef USE_SERVO
  //servo_home = prev_servo_home;
  servo_home = false;
#endif
  Serial.println("音声認識終了");
  Serial.println("音声認識結果");
  if(ret != "") {
    Serial.println(ret);
    robot->chat(ret, base64_buf, deviceActions);
    restore_post_chat_expression();
    servo_home = true;
  } else {
    Serial.println("音声認識失敗");
    if (is_stt_backend_error()) {
      setActivityExpression(Expression::Confusion);
      playLocalPrompt("server_error");
    } else {
      setActivityExpression(Expression::Sad);
      avatar.setSpeechText("聞き取れませんでした");
      playLocalPrompt("listening_error");
    }
    delay(2000);
    avatar.setSpeechText("");
    setIdleExpression();
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
  setIdleExpression();
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
  cancel_conversation_action();
  if (alwaysInputSelected || stackchan::conversationSession().enabled()) {
    select_touch_input();
    stackchan::conversationSession().stop();
  }
  if (inputMenu.isOpen()) {
    inputMenu.close();
    avatar.getFace()->setScreenOverlay(nullptr);
    avatar.set_isSubWindowEnable(false);
  }
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
  // Legacy commands must not replace an explicit server-managed session.
  if (alwaysInputSelected) return;
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  if (mode < 0) {
    return;
  }

  sw_tone();
  if (enabled) {
    touchInputOnly = false;  // An explicit legacy wakeword command still works.
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
  if (alwaysInputSelected || is_camera_trigger_active()
      || stackchan::touchActionSession().pendingRetry()) return;
  sw_tone();
  STT_ChatGPT(NULL, true);
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
  if (alwaysInputSelected) return;
  if (robot->m_config.getExConfig().wakeword.type != WAKEWORD_TYPE_SIMPLEVOX) {
    return;
  }
  touchInputOnly = false;  // Explicit legacy registration needs its idle path.
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
  if (inputMenu.isOpen()) {
    close_input_menu();
  }
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
    setActivityExpression(Expression::Happy);
    STT_ChatGPT();
    return true;
  }

#ifdef USE_SERVO
  servo_home = true;
#endif
  setActivityExpression(Expression::Happy);
  robot->chat(query);
  avatar.setSpeechText("");
  restore_post_chat_expression();
  servo_home = true;
  return true;
}

bool AiStackChanMod::display_touch_contact(unsigned count, int16_t x, int16_t y)
{
  if (inputMenu.isOpen()) {
    if (is_camera_trigger_active()) {
      close_input_menu();
      return true;
    }
    const auto action = inputMenu.contact(count, x, y);
    if (action == stackchan::InputModeMenu::Action::None) return true;
    close_input_menu();
    auto& session = stackchan::conversationSession();
    if (action == stackchan::InputModeMenu::Action::SelectTouch) {
      select_touch_input();
      cancel_conversation_action();
      const bool stopped = session.stop();
      avatar.setSpeechText(stopped ? "" : "タッチ\nせつぞく エラー");
      return true;
    }
    if (action == stackchan::InputModeMenu::Action::SelectAlways) {
      if (alwaysInputSelected && session.enabled()) {
        avatar.setSpeechText("");
        return true;  // No restart or extra start event on reselection.
      }
      select_touch_input();
      cancel_conversation_action();
      if (session.phase() != stackchan::ConversationSession::Phase::Off) session.stop();
      alwaysInputSelected = true;
      inputErrorNotified = false;
      inputRetryAtMs = 0;
      if (isOffline || !robot->stt->supportsInterruptible()
          || !robot->tts->supportsCheckedStream()) {
        avatar.setSpeechText("いつでも\nおんせい せっていを\nたしかめてね");
        return true;
      }
      if (!session.start()) {
        report_input_error("せつぞく エラー", true);
        return true;
      }
    }
    avatar.setSpeechText("");
    return true;
  }
  return false;
}

void AiStackChanMod::display_touched(int16_t x, int16_t y)
{
  if (inputMenu.isOpen()) return;  // Modal choices are handled on contact-down.
  if (!box_stt.contain(x, y))
  {
    return;
  }

  if (is_camera_trigger_active()) {
    return;
  }

  if (alwaysInputSelected) {
    auto& session = stackchan::conversationSession();
    if (session.phase() != stackchan::ConversationSession::Phase::Listening) {
      inputRetryAtMs = 0;
      return;  // A failed session must not fall into legacy touch chat.
    }
    if (!session.faceTouch()) {
      report_input_error("せつぞく エラー", true);
    } else {
      avatar.setSpeechText("いつでも");
    }
    return;  // Resume through the server, never through legacy robot->chat().
  }
  if (stackchan::touchActionSession().pendingRetry()) return;
  if (stackchan::touchActionSession().phase() == stackchan::ConversationSession::Phase::Fault) {
    cancel_conversation_action();
    stackchan::touchActionSession().abandon();
  }

  sw_tone();
#if defined(ENABLE_CAMERA)
  avatar.set_isSubWindowEnable(false);
  if(isSubWindowON){
    String base64;
    bool ret = camera_capture_base64(base64);
    STT_ChatGPT(base64.c_str(), true);
  }
  else{
    STT_ChatGPT(NULL, true);
  }
  avatar.set_isSubWindowEnable(isSubWindowON);
#else
  STT_ChatGPT(NULL, true);
#endif
}

void AiStackChanMod::doubleTapped(float ax, float ay, float az)
{
  Serial.printf("Mod double tapped. ax=%.3f ay=%.3f az=%.3f\n", ax, ay, az);
  if (alwaysInputSelected || is_camera_trigger_active()
      || stackchan::touchActionSession().pendingRetry()) {
    return;
  }
#if defined(ARDUINO_M5STACK_ATOMS3R)
  sw_tone();
  STT_ChatGPT(NULL, true);
#endif
}

void AiStackChanMod::idle(void)
{
  if (inputMenu.isOpen() && is_camera_trigger_active()) {
    close_input_menu();
  }
  refreshIdleExpression(is_camera_trigger_active());
  if (startupSpeechClearAtMs != 0 && millis() >= startupSpeechClearAtMs) {
    avatar.setSpeechText("");
    startupSpeechClearAtMs = 0;
  }

  // Let a finger held on the face reach the long-press handler without
  // starting a blocking listen first. Menus pause automatic input, not alarms.
  const bool inputUiActive = inputMenu.isOpen() || M5.Touch.getCount() != 0
      || stackchan::touchActionSession().pendingRetry();

  /// Face detect ///
#if defined(ENABLE_CAMERA)
  bool shouldHandleCamera = isSubWindowON;
#if defined(ENABLE_FACE_DETECT)
  shouldHandleCamera = true;
#endif
  if (shouldHandleCamera && !inputUiActive) {
    //顔が検出されれば音声認識を開始。
    bool isFaceDetected;
    isFaceDetected = camera_capture_and_face_detect();
    if(!isSilentMode){

#if defined(ENABLE_FACE_DETECT)
      if(isFaceDetected && !touchInputOnly){
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
        setActivityExpression(Expression::Happy);
        //delay(2000);
        //avatar.setExpression(Expression::Neutral);
      }
      else{
        setIdleExpression();
      }
#endif
    }
  }
#endif  //ENABLE_CAMERA

  //Wakeword
#if defined(ENABLE_WAKEWORD)
  if (!inputUiActive && !touchInputOnly && !is_camera_trigger_active()) {
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
  }

#if defined(ARDUINO_M5STACK_CORES3)
  if (!inputUiActive && !is_camera_trigger_active()) {
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
    if (inputMenu.isOpen()) {
      close_input_menu();
    }
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

  // The user enabled this session; no wakeword gate before each turn. Even in
  // quiet state send recognized speech to the server, which decides resumption.
  if (!inputUiActive && !is_camera_trigger_active() && alwaysInputSelected) {
    listen_always_input();
  }
}

void AiStackChanMod::select_touch_input(void)
{
  touchInputOnly = true;
  alwaysInputSelected = false;
  inputErrorNotified = false;
  inputRetryAtMs = 0;
  startupSpeechClearAtMs = 0;
#if defined(ENABLE_WAKEWORD)
  mode = 0;
  wakeword_is_enable = false;
#if defined(ARDUINO_M5STACK_CORES3)
  wakeword_enable_required = false;
  wakeword_disable_required = false;
  register_wakeword_required = false;
#endif
#endif
}

void AiStackChanMod::report_input_error(const char* message, bool connectionError)
{
  const String text = String("いつでも\n") + message;
  avatar.setSpeechText(text.c_str());
  if (!inputErrorNotified) playInputErrorPrompt(connectionError);
  finishExpressionActivity(Expression::Doubt);  // Also when audio is suppressed.
  inputErrorNotified = true;
  inputRetryAtMs = millis() + 1000;
}

void AiStackChanMod::handle_always_outcome(stackchan::ConversationSession::Outcome outcome)
{
  auto& session = stackchan::conversationSession();
  using Outcome = stackchan::ConversationSession::Outcome;
  if (outcome == Outcome::Failed || outcome == Outcome::Retry) {
    const bool unsupported = session.error() == stackchan::ConversationSession::Error::UnsupportedAction;
    report_input_error(unsupported ? "おへんじを\nたしかめてね" : "せつぞく エラー", !unsupported);
    return;
  }
  if (outcome == Outcome::Perform) {
    if (!request_conversation_action(session)) {
      playInputErrorPrompt();
      inputErrorNotified = true;
      if (!session.actionCompleted(false)) report_input_error("せつぞく エラー", true);
    }
    return;
  }
  if (outcome == Outcome::Reply) {
    const bool played = robot->speechChecked(session.replyText().c_str());
    if (!played) {
      playInputErrorPrompt();
      inputErrorNotified = true;
    }
    // Both real success and real failure release the v2 reply barrier. An ACK
    // retry never reaches this branch, so it cannot speak the same reply twice.
    if (!session.playbackCompleted(played)) {
      report_input_error("せつぞく エラー", true);
      return;
    }
  }
  inputErrorNotified = false;
  finishExpressionActivity(Expression::Doubt);
  avatar.setSpeechText(session.quiet() ? "いつでも\nきいているよ" : "いつでも");
}

void AiStackChanMod::listen_always_input(void)
{
  auto& session = stackchan::conversationSession();
  using Phase = stackchan::ConversationSession::Phase;
  if (conversation_action_owns(session)) return;  // Its queue owns ACK retries.
  if (inputRetryAtMs != 0 && int32_t(millis() - inputRetryAtMs) < 0) return;
  inputRetryAtMs = 0;
  if (isOffline || !robot->stt->supportsInterruptible() || !robot->tts->supportsCheckedStream()) return;
  if (session.phase() == Phase::Fault) {
    // Protocol/input faults must not strand the selected input mode. Close the
    // old generation once, discard its result, then start fresh next loop.
    // Transient HTTP failures instead retain/retry the original request below.
    session.stop();
    return;
  }
  if (session.phase() == Phase::Off) {
    if (!session.start()) report_input_error("せつぞく エラー", true);
    else handle_always_outcome(stackchan::ConversationSession::Outcome::Listen);
    return;
  }
  if (session.pendingRetry()) {
    inputRetryAtMs = millis() + 1000;
    handle_always_outcome(session.retryPending());
    return;
  }
  if (session.phase() != Phase::Listening) return;
  const String transcript = robot->listenInterruptible();
  if (transcript.isEmpty()) {
    if (is_stt_backend_error()) report_input_error("せつぞく エラー", true);
    return;  // NoSpeech / Cancelled: no TTS, event, log or history entry.
  }
  if (is_camera_trigger_active()) return;
  setActivityExpression(Expression::Doubt);
  const uint32_t started = millis();
  const auto outcome = session.speech(transcript.c_str());
  const uint32_t elapsed = millis() - started;
  if (elapsed < 700) delay(700 - elapsed);  // Same existing chat thinking policy.
  handle_always_outcome(outcome);
}

bool AiStackChanMod::display_long_touched(int16_t x, int16_t y)
{
  if (!box_stt.contain(x, y)) {
    return false;
  }
  // Consume a hold even if it cannot open a menu; do not start STT on release.
  if (is_camera_trigger_active()) {
    return true;
  }
#if defined(ENABLE_WAKEWORD)
  if (mode < 0) {  // Do not interrupt legacy wakeword registration.
    return true;
  }
#endif
  if (!inputMenu.isOpen()) {
    inputMenu.open();
    startupSpeechClearAtMs = 0;
    avatar.setSpeechText("");
    draw_input_menu();
  }
  return true;
}

bool AiStackChanMod::display_flicked(void)
{
  if (!inputMenu.isOpen()) {
    return false;
  }
  return true;  // Keep the two choices open; no hidden flick action.
}

void AiStackChanMod::draw_input_menu(void)
{
  static InputModeMenuView view;
  avatar.set_isSubWindowEnable(false);
  avatar.getFace()->setScreenOverlay(&view);
}

void AiStackChanMod::close_input_menu(void)
{
  inputMenu.close();
  avatar.getFace()->setScreenOverlay(nullptr);
  avatar.set_isSubWindowEnable(false);
  if (isQrDrawing) {
    String url = String("http://") + WiFi.localIP().toString();
    avatar.updateSubWindowQrcode(url);
    avatar.set_isSubWindowEnable(true);
  }
#if defined(ENABLE_CAMERA)
  else if (isSubWindowON) {
    // The next idle pass supplies a fresh camera frame using its usual size.
    avatar.getFace()->subWindowPos->setPosition(0, 0);
    avatar.getFace()->subWindowPos->setSize(0, 0);
    avatar.set_isSubWindowEnable(true);
  }
#endif
}

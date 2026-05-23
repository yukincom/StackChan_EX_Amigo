#include <Arduino.h>
//#include <FS.h>
#include <SD.h>
#include <SPIFFS.h>
#include "share/Version.h"
#include "share/Mutex.h"
#include "share/SDUtil.h"
#include "share/DefaultParams.h"
#include <M5Unified.h>
#include <nvs.h>
#include <Avatar.h>
#include <faces/CatFace.h>
#include "StackchanExConfig.h" 
#include "Robot.h"
#include "mod/ModManager.h"
#include "mod/ModBase.h"
#include "mod/AiStackChan/AiStackChanMod.h"
#include "mod/Pomodoro/PomodoroMod.h"
#include "mod/PhotoFrame/PhotoFrameMod.h"
#include "mod/StatusMonitor/StatusMonitorMod.h"
#include "mod/VolumeSetting/VolumeSettingMod.h"
#include "mod/QRdisplay/QRdisplayMod.h"

#include "driver/PlayMP3.h"   //lipSync
#include "driver/TapDetect.h"

#include "SpiRamJsonDocument.h"
#include <ESP8266FtpServer.h>

#include "llm/ChatGPT/ChatGPT.h"
#include "llm/ChatGPT/FunctionCall.h"
#include "llm/ChatHistory.h"

#include "WebAPI.h"

#if defined( ENABLE_CAMERA )
#include "driver/Camera.h"
#endif    //ENABLE_CAMERA

#include "driver/WatchDog.h"
#include "SDUpdater.h"
#include "DebugTools.h"

#if defined(USE_AUDIO_MODULE)
#include "driver/M5AudioModule.h"
#endif

StackchanExConfig system_config;
Robot* robot;
bool isOffline = false;
volatile bool g_cameraBusy = false;


// NTP接続情報　NTP connection information.
const char* NTPSRV      = "ntp.jst.mfeed.ad.jp";    // NTPサーバーアドレス NTP server address.
const long  GMT_OFFSET  = 9 * 3600;                 // GMT-TOKYO(時差９時間）9 hours time difference.
const int   DAYLIGHT_OFFSET = 0;                    // サマータイム設定なし No daylight saving time setting

//bool servo_home = false;
bool servo_home = true;

using namespace m5avatar;
Avatar avatar;
Face* customFace;
const Expression expressions_table[] = {
  Expression::Neutral,
  Expression::Happy,
  Expression::Sleepy,
  Expression::Doubt,
  Expression::Sad,
  Expression::Angry
};

FtpServer ftpSrv;   //set #define FTP_DEBUG in ESP8266FtpServer.h to see ftp verbose on serial





void lipSync(void *args)
{
  float gazeX, gazeY;
  int level = 0;
  DriveContext *ctx = (DriveContext *)args;
  Avatar *avatar = ctx->getAvatar();
  for (;;)
  {
    level = robot->tts->getLevel();
    if(level<100) level = 0;
    if(level > 15000)
    {
      level = 15000;
    }
    float open = (float)level/15000.0;
    avatar->setMouthOpenRatio(open);
    avatar->getGaze(&gazeY, &gazeX);
    avatar->setRotation(gazeX * 5);
    delay(100);
  }
}


void servo(void *args)
{
  float gazeX, gazeY;
  DriveContext *ctx = (DriveContext *)args;
  Avatar *avatar = ctx->getAvatar();
  for (;;)
  {
#ifdef USE_SERVO
    if(!servo_home)
    {
      avatar->getGaze(&gazeY, &gazeX);
      robot->servo->moveToGaze((int)(15.0 * gazeX), (int)(10.0 * gazeY));
    } else {
      robot->servo->moveToOrigin();
    }
#endif
    delay(5000);
  }
}

void battery_check(void *args) {
  DriveContext *ctx = (DriveContext *)args;
  Avatar *avatar = ctx->getAvatar();
  for (;;)
  {
    if (g_cameraBusy) {
      delay(1000);
      continue;
    }

    int32_t batteryLevel = M5.Power.getBatteryLevel();
    if((batteryLevel < 95) && (batteryLevel != 0)){
      avatar->setBatteryIcon(true);
      avatar->setBatteryStatus(M5.Power.isCharging(), batteryLevel);
    }
    else{
      avatar->setBatteryIcon(false);    
    }
    delay(60000);
  }
}

bool Wifi_connection_check() {
  unsigned long start_millis = millis();

  // 前回接続時情報で接続する
  while (WiFi.status() != WL_CONNECTED) {
    M5.Display.print(".");
    Serial.print(".");
    delay(1000);
    // 5秒以上接続できなかったら抜ける
    if ( 5000 < (millis() - start_millis) ) {
      //break;
      return false;
    }
  }
  return true;
}

bool WifiSmartConfig() {
  unsigned long start_millis = millis();
  WiFi.mode(WIFI_STA);
  WiFi.beginSmartConfig();
  M5.Display.println("Waiting for SmartConfig");
  Serial.println("Waiting for SmartConfig");
  while (!WiFi.smartConfigDone()) {
    delay(1000);
    M5.Display.print("#");
    Serial.print("#");
    // 30秒以上接続できなかったら抜ける
    if ( 30000 < millis() - start_millis) {
      Serial.println("");
      //Serial.println("Reset");
      //ESP.restart();
      return false;
    }
  }
  return true;
}

void time_sync(const char* ntpsrv, long gmt_offset, int daylight_offset) {
  struct tm timeInfo; 
  char buf[60];

  configTime(gmt_offset, daylight_offset, ntpsrv);          // NTPサーバと同期

  if (getLocalTime(&timeInfo)) {                            // timeinfoに現在時刻を格納
    Serial.print("NTP : ");                                 // シリアルモニターに表示
    Serial.println(ntpsrv);                                 // シリアルモニターに表示

    sprintf(buf, "%04d-%02d-%02d %02d:%02d:%02d\n",     // 表示内容の編集
    timeInfo.tm_year + 1900, timeInfo.tm_mon + 1, timeInfo.tm_mday,
    timeInfo.tm_hour, timeInfo.tm_min, timeInfo.tm_sec);

    Serial.println(buf);                                    // シリアルモニターに表示
  }
  else {
    Serial.print("NTP Sync Error ");                        // シリアルモニターに表示
  }
}



ModBase* init_mod(void)
{
  ModBase* mod;
  if(!isOffline || robot->isAllOfflineService()){
    add_mod(new AiStackChanMod(isOffline));
  }
  add_mod(new StatusMonitorMod());
  add_mod(new VolumeSettingMod());
  //add_mod(new PomodoroMod(isOffline));
  //add_mod(new PhotoFrameMod(isOffline));
  //add_mod(new QRdisplayMod());
  mod = get_current_mod();
  mod->init();
  return mod;
}


void sw_tone()
{
  enterMutexAudio();
  M5.Mic.end();
  M5.Speaker.begin();
  delay(300);     // AtomS3Rはこのdelayがないと鳴らないときがある
  M5.Speaker.tone(1000, 100);
  delay(500);

  M5.Speaker.end();
  M5.Mic.begin();
  exitMutexAudio();
}
  
void alarm_tone()
{
  enterMutexAudio();
  M5.Mic.end();
  M5.Speaker.begin();

  for(int i=0; i<5; i++){
    M5.Speaker.tone(1200, 50);
    delay(100);
    M5.Speaker.tone(1200, 50);
    delay(100);
    M5.Speaker.tone(1200, 50);
    delay(1000);  
  }

  M5.Speaker.end();
  M5.Mic.begin();
  exitMutexAudio();
}

void init_mic_spk()
{
#if defined(USE_AUDIO_MODULE)
  initAudioModule();
#endif

  {
    auto micConfig = M5.Mic.config();
    //micConfig.stereo = false;
    micConfig.sample_rate = 16000;
#if defined(USE_AUDIO_MODULE)
    micConfig.pin_data_in = SYS_I2S_DIN_PIN;
    micConfig.pin_bck = SYS_I2S_SCLK_PIN;
    micConfig.pin_mck = SYS_I2S_MCLK_PIN;
    micConfig.pin_ws = SYS_I2S_LRCK_PIN;
#endif
    M5.Mic.config(micConfig);
  }
  M5.Mic.begin();

  { /// custom setting
    auto spk_cfg = M5.Speaker.config();
    /// Increasing the sample_rate will improve the sound quality instead of increasing the CPU load.
    spk_cfg.sample_rate = 64000; // default:64000 (64kHz)  e.g. 48000 , 50000 , 80000 , 96000 , 100000 , 128000 , 144000 , 192000 , 200000
    spk_cfg.task_pinned_core = APP_CPU_NUM;

#if defined(USE_AUDIO_MODULE)
    spk_cfg.pin_data_out = SYS_I2S_DOUT_PIN;
    spk_cfg.pin_bck = SYS_I2S_SCLK_PIN;
    spk_cfg.pin_mck = SYS_I2S_MCLK_PIN;
    spk_cfg.pin_ws = SYS_I2S_LRCK_PIN;
#endif
    M5.Speaker.config(spk_cfg);
  }
  //M5.Speaker.begin();
}

void setup()
{
  auto cfg = M5.config();

#if defined(ARDUINO_M5STACK_ATOMS3R)
  cfg.internal_spk = false;
  cfg.internal_mic = false;
  cfg.external_speaker.atomic_echo = true;
#endif
  cfg.serial_baudrate = 115200;   //M5Unified 0.1.17からデフォルトが0になったため設定
  M5.begin(cfg);
  gpio_reset_pin(GPIO_NUM_2);
  delay(100);

  /// シリアル出力のログレベルを VERBOSEに設定
  //M5.Log.setLogLevel(m5::log_target_serial, ESP_LOG_VERBOSE);


#if defined(ARDUINO_M5STACK_ATOMS3R)
  M5.Lcd.setTextSize(2);
  M5.Lcd.printf("Ver.%s\n", FW_VERSION);
#else
  M5.Lcd.setFont(&fonts::lgfxJapanGothic_20);
  M5.Lcd.setTextSize(1);
  M5.Lcd.println("Stack-chan Ex Amigo [・＿・]");
  M5.Lcd.printf("Firmware Version: %s\n", FW_VERSION);
#endif

  initMutex();

#if defined(ENABLE_SD_UPDATER)
  // ***** for SD-Updater *********************
  SDU_lobby("AiStackChanEx");
  // ******************************************
#endif

  //auto brightness = M5.Display.getBrightness();
  //Serial.printf("Brightness: %d\n", brightness);

  init_mic_spk();

  /// settings
#if defined(ARDUINO_M5STACK_ATOMS3R)
  if (SPIFFS.begin()) {
    // この関数ですべてのYAMLファイル(Basic, Secret, Extend)を読み込む
    system_config.loadConfig(SPIFFS, "/SC_ExConfig.yaml", 2048,
                                     "/SC_SecConfig.yaml", 2048,
                                     "/SC_BasicConfig.yaml", 2048);
#else
  if (SD.begin(GPIO_NUM_4, SPI, 25000000)) {
    // この関数ですべてのYAMLファイル(Basic, Secret, Extend)を読み込む
    system_config.loadConfig(SD, "/app/AiStackChanEx/SC_ExConfig.yaml");
#endif
    // Wifi設定読み込み
    wifi_s* wifi_info = system_config.getWiFiSetting();
    Serial.printf("\nSSID: %s\n",wifi_info->ssid.c_str());
    Serial.printf("Key: %s\n",wifi_info->password.c_str());

    // 前回設定で接続
    Serial.println("Connecting to WiFi");
    WiFi.disconnect();
    WiFi.softAPdisconnect(true);
    WiFi.mode(WIFI_STA);
    WiFi.begin();
    if(Wifi_connection_check()){
      Serial.println("Successfully connected to Wi-Fi using the previous settings.");
    }else{
      // 前回設定での接続に失敗。SDカード設定による接続にトライ。
      Serial.println("The previous WiFi connection failed. Attempting to connect using the SD card settings.");
      if(wifi_info->ssid.length() == 0){
        // SDカード設定の取得に失敗。Smart Configをスタート。
        Serial.println("Can't get WiFi settings. Start Smart Config.");
        if(!WifiSmartConfig()){
          // Smart Config失敗。オフラインモード。
          Serial.println("Smart Config failed. Running in offline mode.");
          isOffline = true;
        }
      }else{
        WiFi.begin(wifi_info->ssid.c_str(), wifi_info->password.c_str());
        if(Wifi_connection_check()){
          // SDカード設定による接続に成功。
          Serial.println("Successfully established a Wi-Fi connection via the SD card settings.");
        }else{
          // SDカード設定による接続に失敗。Smart Configをスタート。
          Serial.println("WiFi connection failed due to SD card settings. Start Smart Config.");
          if(!WifiSmartConfig()){
            // Smart Config失敗。オフラインモード。
            Serial.println("Smart Config failed. Running in offline mode.");
            isOffline = true;
          }
        }
      }
    }

    if(!isOffline){
      Serial.println(WiFi.localIP());
      M5.Lcd.println(WiFi.localIP());
      delay(1000);

      //Webサーバ設定
      init_web_server();
      //FTPサーバ設定（SPIFFS用）
      ftpSrv.begin("stackchan","stackchan");    //username, password for ftp.  set ports in ESP8266FtpServer.h  (default 21, 50009 for PASV)
      Serial.println("FTP server started");
      M5.Lcd.println("FTP server started");

      //時刻同期
      time_sync(NTPSRV, GMT_OFFSET, DAYLIGHT_OFFSET);
    }else{
      M5.Lcd.print("Can't connect to WiFi. Start offline mode.\n");
    }

    robot = new Robot(system_config);

    //SD.end();
  } else {
    M5.Lcd.print("Failed to load SD card settings. System reset after 5 seconds.");
    delay(5000);
    ESP.restart();
    //WiFi.begin();
  }
  
  mp3_init();

  //mod設定
  init_mod();

#if defined(ARDUINO_M5STACK_ATOMS3R)
#if defined(CAT_FACE)
  customFace = new CatFace();
  avatar.setFace(customFace);
#endif
  avatar.setScale(0.5);
  avatar.setPosition(-56, -96);
  avatar.init();
#else
  //avatar.init();
  avatar.init(16);
#endif

  avatar.addTask(lipSync, "lipSync", 2048, 2);
  avatar.addTask(servo, "servo", 2048);
  avatar.addTask(battery_check, "battery_check", 2048);
  avatar.setSpeechFont(&fonts::efontJA_16);
  M5.Display.setBrightness(96);

  Serial.printf("Speaker volume (yaml): %d\n", system_config.getExConfig().audio.speaker_volume);
  if(0 != system_config.getExConfig().audio.speaker_volume){
    robot->spk_volume = system_config.getExConfig().audio.speaker_volume;
  }else{
    robot->spk_volume = DEFAULT_SPEAKER_VOLUME;
  }
  Serial.printf("Speaker volume (set): %d\n", robot->spk_volume);
  M5.Speaker.setVolume(robot->spk_volume);

#if defined(ENABLE_CAMERA)
  avatar.set_isSubWindowEnable(isSubWindowON);
#endif

#if defined(ENABLE_TAP_DETECT)
  invokeDoubleTapDetectTask();
#endif

  //init_watchdog();

  //ヒープメモリ残量確認(デバッグ用)
  check_heap_free_size();
  check_heap_largest_free_block();

}



void loop()
{
  //get_elapsed_time_micro("loop() start");
  M5.update();
  //get_elapsed_time_micro("M5.update time");
  ModBase* mod = get_current_mod();
  mod->idle();
  //get_elapsed_time_micro("Mod idle time");

  if (M5.BtnA.wasPressed())
  {
    mod->btnA_pressed();
  }

  if (M5.BtnA.pressedFor(2000))
  {
    mod->btnA_longPressed();
  }

  if (M5.BtnB.wasPressed())
  {
    mod->btnB_pressed();
  }

  if (M5.BtnB.pressedFor(2000))
  {
    mod->btnB_longPressed();
  }

  if (M5.BtnC.wasPressed())
  {
    mod->btnC_pressed();
  }

#if defined(ARDUINO_M5STACK_Core2) || defined( ARDUINO_M5STACK_CORES3 )
  static bool touch_tracking = false;
  static bool touch_long_handled = false;
  static bool touch_suppressed = false;
  static uint32_t touch_started_at = 0;
  static int16_t touch_start_x = 0;
  static int16_t touch_start_y = 0;
  auto count = M5.Touch.getCount();
  if (count)
  {
    auto t = M5.Touch.getDetail();
    if (!touch_tracking || t.wasPressed())
    {
      touch_tracking = true;
      touch_long_handled = false;
      touch_suppressed = is_camera_trigger_active();
      touch_started_at = millis();
      touch_start_x = t.x;
      touch_start_y = t.y;
    }

    if (!touch_suppressed && !touch_long_handled && (millis() - touch_started_at >= 2000))
    {
      touch_long_handled = mod->display_long_touched(touch_start_x, touch_start_y);
    }

    if (!touch_suppressed && t.wasFlicked())
    {
      touch_long_handled = true;
      int16_t dx = t.distanceX();
      int16_t dy = t.distanceY();

      // detect flick right/left
      if(abs(dx) >= abs(dy))
      {
        if(dx > 0){
          //Serial.println("Right flicked");
          change_mod(true);
        }
        else{
          //Serial.println("Left flicked");
          change_mod();
        }
      }
    }
  }
  else if (touch_tracking)
  {
    if (!touch_suppressed && !touch_long_handled)
    {
      mod->display_touched(touch_start_x, touch_start_y);
    }
    touch_tracking = false;
    touch_long_handled = false;
    touch_suppressed = false;
  }
#endif

#if defined(ENABLE_TAP_DETECT)
  if(doubleTapDetected){
    Serial.println("loop(): Double tap detected");
    mod->doubleTapped(detectedAcc[0], detectedAcc[1], detectedAcc[2]);
    doubleTapDetected = false;
  }

  // Modで重い処理をしている場合はダブルタップ検出を停止する
  if(mod->isBusy()){
    stopDoubleTapDetectTask();
  }else{
    resumeDoubleTapDetectTask();
  }
#endif
  //get_elapsed_time_micro("Callback process time");

  if(!isOffline){
    web_server_handle_client();
    process_play_request();
    process_camera_trigger_request();
    ftpSrv.handleFTP();
  }

  //get_elapsed_time_micro("Web event process time");
  
  //reset_watchdog();
}

#include "Robot.h"
#include "tts/OpenAITTS.h"
#include "stt/Whisper.h"
#include "llm/LLMBase.h"
#include "llm/ChatGPT/ChatGPT.h"
#include "Avatar.h"

using namespace m5avatar;

extern Avatar avatar;
extern bool servo_home;

Robot::Robot(StackchanExConfig& config) : m_config(config)
{
  // Servo setting
  //
#ifdef USE_SERVO
  servo = new ServoCustom();
  Serial.printf("[SERVO] begin x_pin=%d y_pin=%d x_center=%d y_center=%d x_offset=%d y_offset=%d type=%d\n",
                config.getServoInfo(AXIS_X)->pin,
                config.getServoInfo(AXIS_Y)->pin,
                config.getServoInfo(AXIS_X)->start_degree,
                config.getServoInfo(AXIS_Y)->start_degree,
                config.getServoInfo(AXIS_X)->offset,
                config.getServoInfo(AXIS_Y)->offset,
                (int)config.getServoType());
  servo->begin(config.getServoInfo(AXIS_X)->pin, config.getServoInfo(AXIS_X)->start_degree,
              config.getServoInfo(AXIS_X)->offset,
              config.getServoInfo(AXIS_Y)->pin, config.getServoInfo(AXIS_Y)->start_degree,
              config.getServoInfo(AXIS_Y)->offset,
              (ServoType)config.getServoType());
#endif

  // TakaoBase setting 
  //
  // 設定ファイルのTakaoBaseがtrueの場合に、TakaoBaseのUSBからの給電でバッテリーを充電できるようにする
  // ただし、この設定ではバッテリーからの給電／横のUSBからの給電ではサーボが動かない
  M5.Power.setExtOutput(!config.getUseTakaoBase());

  // AI service setting
  //
  initLLM(config);
  initTTS(config);
  initSTT(config);

}

bool Robot::isAllOfflineService()
{
  return llm->isOfflineService && stt->isOfflineService && tts->isOfflineService;
}

void Robot::initLLM(StackchanExConfig& config){
  int llm_type = config.getExConfig().llm.type;
  api_keys_s* api_key = config.getAPISetting();

  llm_param_t llm_param;
  llm_param.api_key = api_key->ai_service;
  llm_param.llm_conf = config.getExConfig().llm;

  switch(llm_type){
  case LLM_TYPE_CHATGPT:
    llm = new ChatGPT(llm_param);
    break;
  default:
    Serial.printf("Unsupported LLM type %d. Fallback to ChatGPT wrapper.\n", llm_type);
    llm = new ChatGPT(llm_param);
  }
}


void Robot::initSTT(StackchanExConfig& config){
  int stt_type = config.getExConfig().stt.type;
  api_keys_s* api_key = config.getAPISetting();

  stt_param_t stt_param;
  stt_param.api_key = api_key->stt;
  stt_param.stt_conf = config.getExConfig().stt;
  
  switch(stt_type){
  case STT_TYPE_OPENAI_WHISPER:
    stt = new Whisper(stt_param);
    break;
  default:
    Serial.printf("Unsupported STT type %d. Fallback to Whisper wrapper.\n", stt_type);
    stt = new Whisper(stt_param);
  }

}

void Robot::initTTS(StackchanExConfig& config){
  int tts_type = config.getExConfig().tts.type;
  api_keys_s* api_key = config.getAPISetting();

  tts_param_t tts_param;
  tts_param.api_key = api_key->tts;
  tts_param.model = config.getExConfig().tts.model;
  tts_param.voice = config.getExConfig().tts.voice;
  tts_param.base_url = config.getExConfig().tts.base_url;
  switch(tts_type){
  case TTS_TYPE_OPENAI:
    tts_param.api_key = api_key->ai_service;    //API KeyはChatGPTと共通
    tts = new OpenAITTS(tts_param);
    break;
  default:
    Serial.printf("Unsupported TTS type %d. Fallback to OpenAI-compatible TTS wrapper.\n", tts_type);
    tts_param.api_key = api_key->ai_service;
    tts = new OpenAITTS(tts_param);
  }

}

void Robot::speech(String text)
{
  if(text != ""){
    servo_home = false;
    avatar.setExpression(Expression::Neutral);

    tts->stream(text);

    avatar.setExpression(Expression::Neutral);
    servo_home = true;
  }
}

String Robot::listen()
{
  String ret = stt->speech_to_text();
  return ret;
}

void Robot::chat(String text, const char *base64_buf)
{
  llm->chat(text, base64_buf);
}

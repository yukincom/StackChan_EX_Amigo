#if defined(ENABLE_CAMERA)

#include <Arduino.h>
#include <M5Unified.h>
#include <Avatar.h>
#include "Camera.h"
#include <SPIFFS.h>
#include <WiFiClient.h>
#include <base64.h>
#include "DebugTools.h"
#include "share/HttpUtil.h"
using namespace m5avatar;
extern Avatar avatar;


bool isSubWindowON = false;
bool isSilentMode = false;
static bool s_camera_initialized = false;

static void restore_m5_i2c_bus()
{
  M5.In_I2C.begin();
  delay(50);
}

static bool is_archive_mode(const char* mode)
{
  return mode != nullptr && strcmp(mode, "archive") == 0;
}

static framesize_t frame_size_for_mode(const char* mode)
{
  return is_archive_mode(mode) ? FRAMESIZE_QVGA : FRAMESIZE_QQVGA;
}

static int jpeg_quality_for_mode(const char* mode)
{
  return is_archive_mode(mode) ? 80 : 60;
}

static String url_encode_component(const String& input)
{
  String encoded;
  const char* hex = "0123456789ABCDEF";
  for (size_t i = 0; i < input.length(); ++i) {
    unsigned char c = static_cast<unsigned char>(input[i]);
    if ((c >= '0' && c <= '9') ||
        (c >= 'A' && c <= 'Z') ||
        (c >= 'a' && c <= 'z') ||
        c == '-' || c == '_' || c == '.' || c == '~') {
      encoded += static_cast<char>(c);
    } else {
      encoded += '%';
      encoded += hex[(c >> 4) & 0x0F];
      encoded += hex[c & 0x0F];
    }
  }
  return encoded;
}

static bool parse_http_url(const String& url, String& host, uint16_t& port, String& path)
{
  const String http_prefix = "http://";
  if (!url.startsWith(http_prefix)) {
    return false;
  }

  String remainder = url.substring(http_prefix.length());
  int slash_pos = remainder.indexOf('/');
  String host_port = slash_pos >= 0 ? remainder.substring(0, slash_pos) : remainder;
  path = slash_pos >= 0 ? remainder.substring(slash_pos) : "/";
  if (path == "") {
    path = "/";
  }

  int colon_pos = host_port.indexOf(':');
  if (colon_pos >= 0) {
    host = host_port.substring(0, colon_pos);
    port = static_cast<uint16_t>(host_port.substring(colon_pos + 1).toInt());
  } else {
    host = host_port;
    port = 80;
  }

  return host.length() > 0 && port > 0;
}

static int post_jpeg_with_query(
  const String& upload_url,
  const uint8_t* jpg_buf,
  size_t jpg_buf_len,
  bool allow_write_wait,
  bool& body_sent_completely
)
{
  String host;
  uint16_t port = 0;
  String path;
  int code = -1;
  body_sent_completely = false;

  if (!parse_http_url(upload_url, host, port, path)) {
    Serial.printf("[VISION] invalid upload url: %s\n", upload_url.c_str());
    return -1;
  }

  WiFiClient client;
  client.setTimeout(30000);

  if (!client.connect(host.c_str(), port)) {
    Serial.printf("[VISION] connect failed: %s:%u\n", host.c_str(), port);
    return -1;
  }

  client.printf(
      "POST %s HTTP/1.0\r\n"
      "Host: %s\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: %u\r\n"
      "Connection: close\r\n\r\n",
      path.c_str(),
      host.c_str(),
      static_cast<unsigned>(jpg_buf_len));

  size_t sent = 0;
  unsigned long upload_deadline = millis() + 30000;
  while (sent < jpg_buf_len) {
    size_t chunk = (jpg_buf_len - sent) > 1024 ? 1024 : (jpg_buf_len - sent);
    size_t written = client.write(jpg_buf + sent, chunk);
    if (written == 0) {
      if (!allow_write_wait || !client.connected() || millis() > upload_deadline) {
        Serial.printf("[VISION] upload write failed at %u/%u\n",
                      static_cast<unsigned>(sent),
                      static_cast<unsigned>(jpg_buf_len));
        break;
      }
      delay(10);
      continue;
    }
    sent += written;
    upload_deadline = millis() + 30000;
  }

  body_sent_completely = (sent == jpg_buf_len);
  if (body_sent_completely) {
    client.flush();
  }

  // /vision/upload は server 側で非同期処理に流し、即 200 を返す想定。
  // ここで長く待ちすぎると、応答済みでも M5 側だけ 30 秒近く足止めされることがある。
  unsigned long wait_start = millis();
  while (!client.available() && millis() - wait_start < 5000) {
    if (!client.connected() && millis() - wait_start > 300) {
      break;
    }
    delay(1);
  }

  if (client.available()) {
    String status_line = client.readStringUntil('\n');
    status_line.trim();
    if (status_line.startsWith("HTTP/1.")) {
      int first_space = status_line.indexOf(' ');
      if (first_space >= 0) {
        code = status_line.substring(first_space + 1).toInt();
      }
    }
  } else if (body_sent_completely) {
    Serial.println("[VISION] upload response timeout, but body sent completely; assuming accepted");
    code = 202;
  } else {
    Serial.println("[VISION] upload response timeout");
  }
  client.stop();
  return code;
}

static camera_config_t camera_config = {
    .pin_pwdn     = -1,
    .pin_reset    = -1,
    .pin_xclk     = 2,
    .pin_sscb_sda = 12,
    .pin_sscb_scl = 11,

    .pin_d7 = 47,
    .pin_d6 = 48,
    .pin_d5 = 16,
    .pin_d4 = 15,
    .pin_d3 = 42,
    .pin_d2 = 41,
    .pin_d1 = 40,
    .pin_d0 = 39,

    .pin_vsync = 46,
    .pin_href  = 38,
    .pin_pclk  = 45,

    .xclk_freq_hz = 20000000,
    .ledc_timer   = LEDC_TIMER_3,
    .ledc_channel = LEDC_CHANNEL_6,

    .pixel_format = PIXFORMAT_RGB565,
    //.pixel_format = PIXFORMAT_JPEG,
    //.frame_size   = FRAMESIZE_QVGA,   // QVGA(320x240)
    .frame_size   = FRAMESIZE_QVGA,  // QVGA(320x240)
    .jpeg_quality = 0,
    //.fb_count     = 2,
    .fb_count     = 1,
    .fb_location  = CAMERA_FB_IN_PSRAM,
    .grab_mode    = CAMERA_GRAB_WHEN_EMPTY,
    .sccb_i2c_port = 1,
};

esp_err_t camera_init(void){

    if (s_camera_initialized) {
        return ESP_OK;
    }

    //initialize the camera
    esp_camera_deinit();
    gpio_reset_pin(GPIO_NUM_2);
    delay(100);
    M5.In_I2C.release();
    delay(200);
    esp_err_t err = esp_camera_init(&camera_config);
    if (err != ESP_OK) {
        //Serial.println("Camera Init Failed");
        M5.Display.println("Camera Init Failed");
        esp_camera_deinit();
        gpio_reset_pin(GPIO_NUM_2);
        restore_m5_i2c_bus();
        delay(200);
        return err;
    }

    sensor_t *s = esp_camera_sensor_get();
    s->set_hmirror(s, 0);        // 左右反転
    s_camera_initialized = true;

    return ESP_OK;
}

void camera_deinit(void)
{
  if (!s_camera_initialized) {
    return;
  }

  esp_camera_deinit();
  gpio_reset_pin(GPIO_NUM_2);
  restore_m5_i2c_bus();
  delay(50);
  s_camera_initialized = false;
}

static void draw_face_boxes(fb_data_t *fb, std::list<dl::detect::result_t> *results, int face_id)
{
    int x, y, w, h;
    uint32_t color = FACE_COLOR_YELLOW;
    if (face_id < 0)
    {
        color = FACE_COLOR_RED;
    }
    else if (face_id > 0)
    {
        color = FACE_COLOR_GREEN;
    }
    if(fb->bytes_per_pixel == 2){
        //color = ((color >> 8) & 0xF800) | ((color >> 3) & 0x07E0) | (color & 0x001F);
        color = ((color >> 16) & 0x001F) | ((color >> 3) & 0x07E0) | ((color << 8) & 0xF800);
    }
    int i = 0;
    for (std::list<dl::detect::result_t>::iterator prediction = results->begin(); prediction != results->end(); prediction++, i++)
    {
        // rectangle box
        x = (int)prediction->box[0];
        y = (int)prediction->box[1];

        // yが負の数のときにfb_gfx_drawFastHLine()でメモリ破壊してリセットする不具合の対策
        if(y < 0){
           y = 0;
        }

        w = (int)prediction->box[2] - x + 1;
        h = (int)prediction->box[3] - y + 1;

        //Serial.printf("x:%d y:%d w:%d h:%d\n", x, y, w, h);

        if((x + w) > fb->width){
            w = fb->width - x;
        }
        if((y + h) > fb->height){
            h = fb->height - y;
        }

        //Serial.printf("x:%d y:%d w:%d h:%d\n", x, y, w, h);

        //fb_gfx_fillRect(fb, x+10, y+10, w-20, h-20, FACE_COLOR_RED);  //モザイク
        fb_gfx_drawFastHLine(fb, x, y, w, color);
        fb_gfx_drawFastHLine(fb, x, y + h - 1, w, color);
        fb_gfx_drawFastVLine(fb, x, y, h, color);
        fb_gfx_drawFastVLine(fb, x + w - 1, y, h, color);

#if TWO_STAGE
        // landmarks (left eye, mouth left, nose, right eye, mouth right)
        int x0, y0, j;
        for (j = 0; j < 10; j+=2) {
            x0 = (int)prediction->keypoint[j];
            y0 = (int)prediction->keypoint[j+1];
            fb_gfx_fillRect(fb, x0, y0, 3, 3, color);
        }
#endif
    }
}

bool camera_capture_and_face_detect(void){
  bool isDetected = false;

  if (!s_camera_initialized && camera_init() != ESP_OK) {
    return false;
  }

  //acquire a frame
  M5.In_I2C.release();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    //Serial.println("Camera Capture Failed");
    M5.Display.println("Camera Capture Failed");
    return ESP_FAIL;
  }

#if defined(ENABLE_FACE_DETECT)
  int face_id = 0;

#if TWO_STAGE
  HumanFaceDetectMSR01 s1(0.1F, 0.5F, 10, 0.2F);
  HumanFaceDetectMNP01 s2(0.5F, 0.3F, 5);
  std::list<dl::detect::result_t> &candidates = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
  std::list<dl::detect::result_t> &results = s2.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3}, candidates);
#else
  HumanFaceDetectMSR01 s1(0.3F, 0.5F, 10, 0.2F);
  std::list<dl::detect::result_t> &results = s1.infer((uint16_t *)fb->buf, {(int)fb->height, (int)fb->width, 3});
#endif


  
  if (results.size() > 0) {
    //Serial.printf("Face detected : %d\n", results.size());

    isDetected = true;

    fb_data_t rfb;
    rfb.width = fb->width;
    rfb.height = fb->height;
    rfb.data = fb->buf;
    rfb.bytes_per_pixel = 2;
    rfb.format = FB_RGB565;

    draw_face_boxes(&rfb, &results, face_id);

  }
#endif  //ENABLE_FACE_DETECT

  if(isSubWindowON){
    avatar.updateSubWindowCam565(fb->buf);
  }

  //return the frame buffer back to the driver for reuse
  esp_camera_fb_return(fb);

  return isDetected;
}



bool camera_capture_base64(String& out)
{
  bool initialized_here = false;
  if (!s_camera_initialized) {
    if (camera_init() != ESP_OK) {
      return false;
    }
    initialized_here = true;
  }

  //acquire a frame
  M5.In_I2C.release();
  camera_fb_t *fb = esp_camera_fb_get();

  if (!fb) {
    Serial.println("Camera Capture Failed");
    if (initialized_here) {
      camera_deinit();
    }
    return false;
  }

  size_t jpg_buf_len = 0;
  uint8_t *jpg_buf   = NULL;
  int ret;
  bool jpeg_converted = frame2jpg(fb, 80, &jpg_buf, &jpg_buf_len);
  esp_camera_fb_return(fb);
  fb = NULL;
  if (!jpeg_converted) {
    Serial.println("JPEG compression failed");
    if (initialized_here) {
      camera_deinit();
    }
    return false;
  }

#if 0 //debug
  File fdst = SPIFFS.open("/capture.jpg", FILE_WRITE);
  if ((ret = fdst.write(jpg_buf, jpg_buf_len)) < jpg_buf_len) {
    Serial.printf("write spiffs failed: %d - %d\n", ret, jpg_buf_len);
    return false;
  }
#endif


  out = base64::encode(jpg_buf, jpg_buf_len);

#if 0 //debug
  fdst = SPIFFS.open("/capture_base64.txt", FILE_WRITE);
  if ((ret = fdst.write((const uint8_t*)out.c_str(), out.length())) < out.length()) {
    Serial.printf("write spiffs failed: %d - %d\n", ret, out.length());
    return false;
  }
#endif

  free(jpg_buf);
  jpg_buf = NULL;

  if (initialized_here) {
    camera_deinit();
  }
  
  return true;
}

bool camera_capture_post_upload(
  const String& base_url,
  const char* requester,
  const char* transcript,
  const char* mode,
  const char* speaker,
  const char* speaker_label
)
{
  if (base_url.isEmpty()) {
    Serial.println("[VISION] base_url is empty");
    return false;
  }

  camera_config.frame_size = frame_size_for_mode(mode);
  const int jpeg_quality = jpeg_quality_for_mode(mode);
  const bool allow_write_wait = is_archive_mode(mode);

  bool initialized_here = false;
  if (!s_camera_initialized) {
    if (camera_init() != ESP_OK) {
      return false;
    }
    initialized_here = true;
  }

  M5.In_I2C.release();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[VISION] Camera capture failed");
    if (initialized_here) {
      camera_deinit();
    }
    return false;
  }

  size_t jpg_buf_len = 0;
  uint8_t *jpg_buf = NULL;
  if (fb->format == PIXFORMAT_JPEG) {
    jpg_buf_len = fb->len;
    jpg_buf = static_cast<uint8_t*>(malloc(jpg_buf_len));
    if (jpg_buf) {
      memcpy(jpg_buf, fb->buf, jpg_buf_len);
    }
  } else {
    frame2jpg(fb, jpeg_quality, &jpg_buf, &jpg_buf_len);
  }
  esp_camera_fb_return(fb);
  fb = NULL;

  if (!jpg_buf || jpg_buf_len == 0) {
    Serial.println("[VISION] JPEG compression failed");
    if (jpg_buf) {
      free(jpg_buf);
    }
    if (initialized_here) {
      camera_deinit();
    }
    return false;
  }

  String upload_url = join_url(base_url, "/vision/upload");
  upload_url += "?r=";
  upload_url += requester && requester[0] ? requester : "user";
  upload_url += "&t=";
  upload_url += url_encode_component(String(transcript ? transcript : ""));
  upload_url += "&s=";
  upload_url += url_encode_component(String(speaker && speaker[0] ? speaker : "master"));
  upload_url += "&sl=";
  upload_url += url_encode_component(String(speaker_label ? speaker_label : ""));

  int code = -1;
  bool body_sent_completely = false;
  const int max_attempts = 2;
  for (int attempt = 1; attempt <= max_attempts; ++attempt) {
    bool sent_this_try = false;
    code = post_jpeg_with_query(upload_url, jpg_buf, jpg_buf_len, allow_write_wait, sent_this_try);
    body_sent_completely = sent_this_try;
    if (code > 0 && code < 300) {
      break;
    }
    if (attempt < max_attempts && !body_sent_completely) {
      Serial.printf("[VISION] retry upload attempt %d/%d\n", attempt + 1, max_attempts);
      delay(150);
      continue;
    }
    break;
  }

  free(jpg_buf);
  jpg_buf = NULL;

  if (initialized_here) {
    camera_deinit();
  }

  Serial.printf("[VISION] upload -> %d\n", code);
  return code > 0 && code < 300;
}

#endif

// xiao-diag.ino: bench test for the XIAO ESP32S3 Sense expansion board (camera, microSD, PDM mic).
// No Wi-Fi, no secrets. Flash: firmware/xiao-clip/flash.sh's FQBN, or
//   arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi xiao-diag && arduino-cli upload ...
// Prints one report, then a press-test line every pass (camera bus, SD, mic level).
#include "esp_camera.h"
#include "FS.h"
#include "SD.h"
#include "Wire.h"
#include "ESP_I2S.h"

I2SClass i2s;

const char* sensorName(uint8_t addr) {
  switch (addr) {
    case 0x30: return "OV2640";
    case 0x3C: return "OV3660 / OV5640";
    case 0x21: return "OV7670 / OV7725";
    default: return "unknown";
  }
}

// The sensor only answers on SCCB (I2C) while it gets a clock, so run XCLK during the scan.
int sccbScan() {
  ledcAttach(10, 20000000, 1);
  ledcWrite(10, 1);
  delay(50);
  Wire.begin(40, 39, 100000);
  int found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  SCCB device at 0x%02X (%s)\n", a, sensorName(a));
      found++;
    }
  }
  Wire.end();
  ledcDetach(10);
  return found;
}

bool cameraTest() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = 15; c.pin_d1 = 17; c.pin_d2 = 18; c.pin_d3 = 16;
  c.pin_d4 = 14; c.pin_d5 = 12; c.pin_d6 = 11; c.pin_d7 = 48;
  c.pin_xclk = 10; c.pin_pclk = 13; c.pin_vsync = 38; c.pin_href = 47;
  c.pin_sccb_sda = 40; c.pin_sccb_scl = 39;
  c.pin_pwdn = -1; c.pin_reset = -1;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_SVGA;
  c.jpeg_quality = 12;
  c.fb_count = 1;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("  esp_camera_init failed: 0x%x (0x105 not found, 0x106 sensor not supported / no ID)\n", err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  Serial.printf("  sensor PID 0x%04X\n", s ? s->id.PID : 0);
  camera_fb_t* fb = NULL;
  for (int i = 0; i < 3; i++) {            // the first frames can be empty while exposure settles
    fb = esp_camera_fb_get();
    if (fb && fb->len > 1000) break;
    if (fb) esp_camera_fb_return(fb);
    fb = NULL;
    delay(200);
  }
  if (!fb) { Serial.println("  no frame captured"); return false; }
  Serial.printf("  frame OK: %ux%u, %u bytes JPEG\n", fb->width, fb->height, fb->len);
  esp_camera_fb_return(fb);
  return true;
}

bool sdTest() {
  if (!SD.begin(21)) {
    Serial.println("  SD.begin(21) failed (no card, not FAT32, or the card slot isn't connected)");
    return false;
  }
  Serial.printf("  card type %d, size %llu MB\n", SD.cardType(), SD.cardSize() / (1024 * 1024));
  File f = SD.open("/diag.txt", FILE_WRITE);
  bool ok = f && f.println("xiao-diag write test");
  if (f) f.close();
  Serial.printf("  write test: %s\n", ok ? "OK" : "FAILED");
  return ok;
}

// RMS and peak of ~0.5 s of 16 kHz audio. A dead or disconnected mic reads flat 0 or a constant.
void micLevel(int& rms, int& peak, int& distinct) {
  const int N = 8000;
  static int16_t buf[N];
  size_t got = i2s.readBytes((char*)buf, sizeof(buf)) / 2;
  double sum = 0; peak = 0; distinct = 0;
  int16_t last = 0;
  for (size_t i = 0; i < got; i++) {
    int v = buf[i];
    sum += (double)v * v;
    if (abs(v) > peak) peak = abs(v);
    if (i && v != last) distinct++;
    last = v;
  }
  rms = got ? (int)sqrt(sum / got) : 0;
}

bool micOK = false;

void setup() {
  Serial.begin(115200);
  delay(2500);
  Serial.println("\n==== xiao-diag: XIAO ESP32S3 Sense expansion board ====");
  Serial.printf("PSRAM: %s (%u KB)\n", psramFound() ? "yes" : "NO", ESP.getPsramSize() / 1024);

  Serial.println("[1] camera bus (SCCB scan with XCLK on)");
  int n = sccbScan();
  if (!n) Serial.println("  nothing answered: the camera ribbon or the board-to-board connector isn't making contact");

  Serial.println("[2] camera init + one frame");
  bool cam = cameraTest();

  Serial.println("[3] microSD");
  bool sd = sdTest();

  Serial.println("[4] PDM mic (CLK 42, DATA 41)");
  i2s.setPinsPdmRx(42, 41);
  micOK = i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  int rms = 0, peak = 0, distinct = 0;
  if (micOK) {
    micLevel(rms, peak, distinct);          // first read flushes start-up noise
    micLevel(rms, peak, distinct);
    Serial.printf("  quiet room: rms %d, peak %d, changing samples %d\n", rms, peak, distinct);
  } else {
    Serial.println("  I2S PDM driver failed to start");
  }
  bool mic = micOK && distinct > 100;

  Serial.printf("RESULT cam=%d sd=%d mic=%d sccb=%d\n", cam, sd, mic, n);
  Serial.println("Press test: camera bus, SD and mic re-checked every pass");
}

// Press test: re-probe the camera bus and the SD card every pass, so pressing on the boards
// shows whether a loose board-to-board contact is the fault (the line flips to yes while pressed).
void loop() {
  int found = sccbScan();
  bool sd = SD.begin(21);
  SD.end();
  int rms = 0, peak = 0, distinct = 0;
  if (micOK) micLevel(rms, peak, distinct);
  Serial.printf("cam bus %-3s  sd %-3s  mic rms %5d peak %5d %s\n", found ? "YES" : "no", sd ? "YES" : "no",
                rms, peak, rms > 300 ? "<-- sound" : "");
  delay(1000);
}

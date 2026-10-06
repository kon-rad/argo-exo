// deck-clip.ino: XIAO ESP32S3 Sense on-demand camera + mic clip.
// Board XIAO_ESP32S3, PSRAM = OPI PSRAM, esp32 core 3.x.
// Records only while the Pi dashboard says so, and only while the Pi is reachable.
#include <WiFi.h>
#include <WiFiMulti.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include "esp_camera.h"
#include "ESP_I2S.h"
#include "FS.h"
#include "SD.h"
#include "time.h"

#include "secrets.h"                          // WIFI_SSID_S, WIFI_PASS_S, DECK_TOKEN_S (not in the vault)
const char* WIFI_SSID = WIFI_SSID_S;
const char* WIFI_PASS = WIFI_PASS_S;
const char* PI_HOST   = "cyberdeck";           // mDNS name without .local
const char* TOKEN     = DECK_TOKEN_S;
const uint16_t PI_PORT = 8081;
const int MIC_CHUNK_S = 10;                    // mic stops at most this long after "off"
const uint32_t LINK_TIMEOUT_MS = 10000;        // no word from the Pi for this long -> stop recording
const int LED_PIN = 1;                         // D0: optional red "recording" LED (330 ohm to GND)

WiFiMulti wifi;
I2SClass i2s;
IPAddress piIP;
bool camOK = false, sdOK = false, micOK = false, mdnsUp = false;
bool wantCam = false, wantMic = false, camWas = false, recording = false, snapPending = false;
long lastSnap = -1;                            // -1 = not synced with the Pi yet
uint32_t photoEveryMs = 30000, lastPhoto = 0, lastControlOk = 0;

bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = 15; c.pin_d1 = 17; c.pin_d2 = 18; c.pin_d3 = 16;
  c.pin_d4 = 14; c.pin_d5 = 12; c.pin_d6 = 11; c.pin_d7 = 48;
  c.pin_xclk = 10; c.pin_pclk = 13; c.pin_vsync = 38; c.pin_href = 47;
  c.pin_sccb_sda = 40; c.pin_sccb_scl = 39;
  c.pin_pwdn = -1; c.pin_reset = -1;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_SVGA;               // 800x600, ~40-80 KB
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;            // fresh frame, not a stale one
  return esp_camera_init(&c) == ESP_OK;
}

String stamp() {
  struct tm t;
  if (getLocalTime(&t, 10)) {
    char b[20];
    strftime(b, sizeof b, "%Y%m%d_%H%M%S", &t);
    return String(b);
  }
  return "nt_" + String(esp_random(), HEX);    // clock not set yet
}

void ensureNet() {
  if (WiFi.status() != WL_CONNECTED) {
    piIP = IPAddress();
    if (wifi.run(5000) != WL_CONNECTED) return;
    configTzTime("JST-9", "pool.ntp.org", "time.google.com");
    if (!mdnsUp) mdnsUp = MDNS.begin("deck-clip");
  }
  if (piIP == IPAddress()) piIP = MDNS.queryHost(PI_HOST, 2000);
}

String piUrl(const String& path) {
  return String("http://") + piIP.toString() + ":" + String(PI_PORT) + path;
}

// Ask the Pi what to do, and report what the clip is doing. Reply: "camera=0 mic=1 interval=30 snap=4".
// snap is a counter: any change since the last poll means "take one photo now".
bool pollControl() {
  if (WiFi.status() != WL_CONNECTED || piIP == IPAddress()) return false;
  HTTPClient http;
  http.begin(piUrl("/control?rec=" + String(recording) + "&cam=" + String(camOK) + "&mic=" + String(micOK) +
                   "&sd=" + String(sdOK) + "&rssi=" + String(WiFi.RSSI())));
  http.setTimeout(2000);
  http.addHeader("X-Deck-Token", TOKEN);
  int code = http.GET();
  String body = code == 200 ? http.getString() : "";
  http.end();
  if (code != 200) { piIP = IPAddress(); return false; }
  int c = 0, m = 0, iv = 30;
  long sn = 0;
  int got = sscanf(body.c_str(), "camera=%d mic=%d interval=%d snap=%ld", &c, &m, &iv, &sn);
  if (got < 2) return false;
  wantCam = c; wantMic = m;
  if (got == 4) {
    if (lastSnap >= 0 && sn != lastSnap) snapPending = true;   // first poll only syncs, never shoots
    lastSnap = sn;
  }
  photoEveryMs = (iv < 5 ? 5 : iv) * 1000UL;
  return true;
}

bool post(const String& name, const uint8_t* data, size_t len) {
  if (WiFi.status() != WL_CONNECTED || piIP == IPAddress()) return false;
  HTTPClient http;
  http.begin(piUrl("/upload/" + name));
  http.addHeader("X-Deck-Token", TOKEN);
  http.addHeader("Content-Type", "application/octet-stream");
  int code = http.POST((uint8_t*)data, len);
  http.end();
  if (code != 200) { piIP = IPAddress(); Serial.printf("upload %s -> %d\n", name.c_str(), code); }
  return code == 200;
}

// Upload now; if that fails and there's a card, keep it for later.
void store(const char* dir, const String& name, const uint8_t* data, size_t len) {
  if (post(name, data, len)) return;
  if (sdOK) {
    File f = SD.open(String(dir) + "/" + name, FILE_WRITE);
    if (f) { f.write(data, len); f.close(); }
    Serial.printf("queued %s/%s\n", dir, name.c_str());
  } else {
    Serial.printf("dropped %s (no SD)\n", name.c_str());
  }
}

// Upload up to maxFiles queued on the card, deleting each one the Pi accepted.
int flushDir(const char* dir, int maxFiles) {
  int sent = 0;
  while (sent < maxFiles) {
    File d = SD.open(dir);
    if (!d) break;
    File f = d.openNextFile();
    if (!f) { d.close(); break; }
    String name = f.name();
    size_t len = f.size();
    uint8_t* buf = (uint8_t*)ps_malloc(len);
    bool ok = buf && f.read(buf, len) == len;
    f.close(); d.close();
    ok = ok && post(name, buf, len);
    free(buf);
    if (!ok) break;
    SD.remove(String(dir) + "/" + name);
    sent++;
  }
  return sent;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  delay(1500);
  camOK = initCamera();
  sdOK = SD.begin(21);
  if (sdOK) { SD.mkdir("/IMG"); SD.mkdir("/AUD"); }
  i2s.setPinsPdmRx(42, 41);                    // PDM mic: CLK 42, DATA 41
  micOK = i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  WiFi.mode(WIFI_STA);
  wifi.addAP(WIFI_SSID, WIFI_PASS);
  Serial.printf("cam=%d sd=%d mic=%d psram=%d\n", camOK, sdOK, micOK, psramFound());
}

void loop() {
  ensureNet();
  if (pollControl()) lastControlOk = millis();
  bool linked = lastControlOk && millis() - lastControlOk < LINK_TIMEOUT_MS;
  bool cam = linked && wantCam && camOK;
  bool mic = linked && wantMic && micOK;
  bool was = recording;
  recording = cam || mic;
  digitalWrite(LED_PIN, recording ? HIGH : LOW);
  if (recording != was) Serial.printf("recording %s (cam=%d mic=%d)\n", recording ? "ON" : "off", cam, mic);

  if (cam && !camWas) lastPhoto = millis() - photoEveryMs;   // first photo right away
  camWas = cam;

  if (mic) {
    String name = "aud_" + stamp() + ".wav";
    size_t n = 0;
    uint8_t* wav = i2s.recordWAV(MIC_CHUNK_S, &n);           // blocks MIC_CHUNK_S seconds
    if (wav) { store("/AUD", name, wav, n); free(wav); }
  }
  if (snapPending && linked && camOK) {                      // one-shot photo from a button tap
    snapPending = false;
    digitalWrite(LED_PIN, HIGH);
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) { store("/IMG", "img_" + stamp() + ".jpg", fb->buf, fb->len); esp_camera_fb_return(fb); }
    Serial.println("snap");
    digitalWrite(LED_PIN, recording ? HIGH : LOW);
  }
  if (cam && millis() - lastPhoto >= photoEveryMs) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb) { store("/IMG", "img_" + stamp() + ".jpg", fb->buf, fb->len); esp_camera_fb_return(fb); }
    lastPhoto = millis();
  }
  if (sdOK && linked) { flushDir("/IMG", 5); flushDir("/AUD", 2); }
  if (!mic) delay(snapPending ? 0 : 1000);                   // idle poll every ~1-2 s
}

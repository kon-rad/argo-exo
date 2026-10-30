// companion-bench.ino: bench test for Ghost (the Exo Argo Companion) on a breadboard, no camera, no Wi-Fi, no secrets.
// XIAO ESP32S3 Sense + 0.96" OLED (SSD1315, 0x3C) + LSM6DSV16X IMU (0x6A/0x6B) + 3 buttons + a WS2812B chain of 3
// (REC -> SKIRT -> HEAD) + MAX98357A I2S amp + microSD on the Sense slot + battery divider on D3.
// Board XIAO_ESP32S3, PSRAM = OPI PSRAM, esp32 core 3.x. Flash with flash.sh.
//
// Wiring and gestures (vault: Areas/argo/argo-exo/plans/exo-argo-companion/exo-argo-companion-build-steps.md phase 1,
// pins and lights in exo-argo-companion-perfboard-buttons-lights.md sections 2-4 and 8):
//   D0 (GPIO1) TALK     to GND: hold = listen, tap = repeat the last answer (a 440 Hz tone on the bench).
//   D1 (GPIO2) CAPTURE  to GND: tap = photo, hold = clip while held, double = video on/off, triple = audio on/off,
//                       any tap stops a latched recording. No camera on the bench: it prints and lights REC only.
//   D2 (GPIO3) ACT      to GND: tap = home, double = next screen, hold 1 s = approve (only while a request is shown;
//                       "ask" on serial makes one), hold 3 s = freeze. The IMU's INT1 shares this line through a
//                       Schottky (imuint on).
//   TALK + ACT held 1 s = PRIVATE on/off. All three held 3 s = POWER OFF; hold TALK 1.5 s to power on.
//   D3 (GPIO4, ADC) battery divider, 2 x 220k from BAT+, 100 nF to GND.
//   D4 (GPIO5) SDA, D5 (GPIO6) SCL.
//   D6 (GPIO43) -> 330 R -> WS2812B chain DIN. Chain order REC (left eye), HEAD (right eye), SKIRT (v6, 2026-10-29). Brightness capped at 25%.
//   D7 (GPIO44) MAX98357A SD: high = on. Held low unless a sound is playing.
//   D8 (GPIO7) / D9 (GPIO8) / D10 (GPIO9): MAX98357A BCLK / LRC / DIN, shared with the microSD's SCK / MISO / MOSI.
//   GPIO21: microSD CS (also the XIAO's user LED, so this sketch never drives that LED).
//   PDM mic on the Sense board: CLK 42, DATA 41.
// Parts that aren't fitted yet are flags, saved in flash: "amp on|off", "imuint on|off". SD and IMU are detected.
// Serial 115200: an I2C scan at boot, the RESULT line, one status line every 2 s, one line per gesture. "help" lists
// the commands.
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <U8g2lib.h>
#include <Preferences.h>
#include "ESP_I2S.h"
#include <math.h>
#include "driver/rtc_io.h"
#include "esp_sleep.h"

// ---------- pins ----------
const int PIN_BTN_TALK    = D0;   // GPIO1, RTC: deep-sleep wake
const int PIN_BTN_CAPTURE = D1;   // GPIO2, RTC: deep-sleep wake
const int PIN_BTN_ACT     = D2;   // GPIO3, RTC: deep-sleep wake; IMU INT1 wired-OR here through a Schottky
const int PIN_BAT         = D3;   // GPIO4, ADC1
const int PIN_SDA = D4;           // GPIO5
const int PIN_SCL = D5;           // GPIO6
const int PIN_PIX     = D6;       // GPIO43 (UART TX at boot: the chain may flash, so it's cleared first thing)
const int PIN_AMP_SD  = D7;       // GPIO44
const int PIN_BUS_SCK = D8;       // GPIO7: amp BCLK / SD SCK
const int PIN_BUS_MISO = D9;      // GPIO8: amp LRC  / SD MISO
const int PIN_BUS_MOSI = D10;     // GPIO9: amp DIN  / SD MOSI
const int PIN_SD_CS   = 21;       // Sense board microSD chip select (= LED_BUILTIN)

// ---------- devices ----------
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
I2SClass mic;                     // PDM in
I2SClass spk;                     // I2S out to the amp, only while a sound plays
Preferences prefs;

bool oledOK = false, imuOK = false, micOK = false, sdOK = false, pixOK = false;
bool ampFitted = false, imuIntOnAct = false;     // flags until the amp and the diode arrive
uint8_t imuAddr = 0;
String i2cFound;
enum Bus { BUS_IDLE, BUS_AUDIO, BUS_STORAGE };
Bus bus = BUS_IDLE;
uint32_t sdHz = 0;                       // the SPI clock the card last mounted at

// ---------- state ----------
enum Rec { REC_NONE, REC_CLIP, REC_VIDEO, REC_AUDIO };
Rec rec = REC_NONE;
bool privateMode = false, frozen = false, talking = false, askPending = false;
int screen = 0;
const int SCREENS = 5;
uint32_t flashUntil = 0;
const char* flashText = "";
char flashSub[28] = "";
uint32_t photos = 0, approvals = 0;
uint32_t photoFlashUntil = 0, approvedUntil = 0, refusedUntil = 0;

float ax = 0, ay = 0, az = 0, aMag = 1, pitchDeg = 0, rollDeg = 0;
bool faceDown = false, still = false;
uint32_t stillSince = 0, lastMotion = 0;
int micRms = 0, micPeak = 0;
int batMv = 0;                    // cell voltage in mV, 0 = no cell
const char* lastWake = "power-on";
bool stopSound = false;          // shake or ACT tap stops a playing sound

void flash(const char* text, const char* sub = "") {
  flashText = text; strncpy(flashSub, sub, sizeof flashSub - 1); flashUntil = millis() + 1200;
}

// ---------- buttons: tap / double / triple, and holds with up to two levels ----------
// A press shorter than HOLD_MS is a click; clicks with less than CLICK_GAP_MS between them add up.
const uint32_t HOLD_MS = 450, CLICK_GAP_MS = 300;
struct Button {
  int pin; const char* name;
  bool down = false; uint32_t downAt = 0, lastChange = 0, lastRelease = 0;
  int count = 0; bool held = false;
  int clicks = 0;                 // set for one loop: 1 = tap, 2 = double, 3 = triple
  bool holdStart = false, holdEnd = false;
  bool suppress = false;          // a chord ate this press
  void begin(int p, const char* n) { pin = p; name = n; pinMode(pin, INPUT_PULLUP); }
  uint32_t heldFor() const { return down ? millis() - downAt : 0; }
  void poll(bool raw) {
    clicks = 0; holdStart = holdEnd = false;
    uint32_t t = millis();
    if (raw != down && t - lastChange > 25) {        // debounce
      down = raw; lastChange = t;
      if (down) { downAt = t; held = false; }
      else {
        if (held) { holdEnd = !suppress; count = 0; }
        else if (!suppress) { count++; lastRelease = t; }
        if (suppress) { count = 0; suppress = false; }
      }
    }
    if (down && !held && t - downAt >= HOLD_MS) { held = true; if (!suppress) holdStart = true; count = 0; }
    if (!down && count > 0 && t - lastRelease > CLICK_GAP_MS) { clicks = min(count, 3); count = 0; }
  }
};
Button bTalk, bCapture, bAct;

// ---------- LSM6DSV16X over raw registers (no library) ----------
const uint8_t IMU_IF_CFG = 0x03, IMU_WHO_AM_I = 0x0F, IMU_CTRL1 = 0x10, IMU_CTRL2 = 0x11, IMU_CTRL3 = 0x12;
const uint8_t IMU_WAKE_UP_SRC = 0x45, IMU_OUTX_L_A = 0x28;
const uint8_t IMU_FUNCTIONS_ENABLE = 0x50, IMU_WAKE_UP_THS = 0x5B, IMU_WAKE_UP_DUR = 0x5C, IMU_MD1_CFG = 0x5E;

bool imuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(imuAddr); Wire.write(reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}
int imuRead(uint8_t reg, uint8_t* buf, size_t n) {
  Wire.beginTransmission(imuAddr); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0;
  int got = Wire.requestFrom((int)imuAddr, (int)n);
  for (int i = 0; i < got; i++) buf[i] = Wire.read();
  return got;
}
// INT1 as an active-low, pulsed wake-up interrupt, so it can pull ACT low through the diode.
// Register bits from the LSM6DSV16X datasheet as remembered: check them on the bench (status shows wu=).
void imuIntConfig(bool on) {
  if (!imuOK) return;
  if (on) {
    imuWrite(IMU_IF_CFG, 0x18);              // PP_OD = 1 (open drain), H_LACTIVE = 1 (active low)
    imuWrite(IMU_WAKE_UP_THS, 0x04);         // ~4 x FS/64 = 125 mg at +-2 g
    imuWrite(IMU_WAKE_UP_DUR, 0x00);
    imuWrite(IMU_FUNCTIONS_ENABLE, 0x80);    // INTERRUPTS_ENABLE
    imuWrite(IMU_MD1_CFG, 0x20);             // INT1_WU
  } else {
    imuWrite(IMU_MD1_CFG, 0x00);
    imuWrite(IMU_FUNCTIONS_ENABLE, 0x00);
  }
}
bool imuWokeUp() {                            // reading WAKE_UP_SRC also clears it
  uint8_t s = 0;
  return imuOK && imuRead(IMU_WAKE_UP_SRC, &s, 1) == 1 && (s & 0x08);
}
bool imuBegin() {
  // A warm reboot (wake from power-off, RESET) once read WHO_AM_I=0x00 although the IMU acked: retry, with a reset between tries.
  for (int attempt = 1; attempt <= 5 && !imuAddr; attempt++) {
    for (uint8_t a : {0x6A, 0x6B}) {
      imuAddr = a;
      uint8_t who = 0;
      if (imuRead(IMU_WHO_AM_I, &who, 1) == 1) {
        Serial.printf("  IMU at 0x%02X WHO_AM_I=0x%02X (LSM6DSV16X is 0x70), try %d\n", a, who, attempt);
        if (who == 0x70) break;
        imuWrite(IMU_CTRL3, 0x01);                  // software reset, then read again
      }
      imuAddr = 0;
    }
    if (!imuAddr) delay(50);
  }
  if (!imuAddr) return false;
  imuWrite(IMU_CTRL3, 0x01); delay(20);           // software reset
  imuWrite(IMU_CTRL3, 0x44);                      // BDU + register auto-increment
  imuWrite(IMU_CTRL1, 0x05);                      // accel 60 Hz, high performance, +-2 g
  imuWrite(IMU_CTRL2, 0x00);                      // gyro off
  return true;
}
void imuPoll() {
  uint8_t b[6];
  if (imuRead(IMU_OUTX_L_A, b, 6) != 6) return;
  const float g = 0.061f / 1000.0f;               // +-2 g: 0.061 mg per LSB
  ax = (int16_t)(b[1] << 8 | b[0]) * g;
  ay = (int16_t)(b[3] << 8 | b[2]) * g;
  az = (int16_t)(b[5] << 8 | b[4]) * g;
  aMag = sqrtf(ax * ax + ay * ay + az * az);
  pitchDeg = atan2f(ax, sqrtf(ay * ay + az * az)) * 180.0f / PI;
  rollDeg  = atan2f(ay, az) * 180.0f / PI;
}

// Pickup (motion after 10 s still), face down (chip down on the bench), shake (big swings).
void motionPoll() {
  if (!imuOK) return;
  uint32_t t = millis();
  bool moving = fabsf(aMag - 1.0f) > 0.08f;
  if (moving) lastMotion = t;
  if (!moving && t - lastMotion > 500) { if (!still) { still = true; stillSince = t; } }
  if (moving && still) {
    if (t - stillSince > 10000) { flash("HELLO", "picked up"); Serial.println("IMU pickup -> wake"); }
    still = false;
  }
  bool down = az < -0.8f;
  if (down != faceDown) {
    faceDown = down;
    privateMode = down;
    Serial.printf("IMU face %s -> %s\n", down ? "down" : "up", down ? "PRIVATE" : "normal");
  }
  static uint32_t swings = 0, firstSwing = 0;
  if (aMag > 2.0f) {
    if (t - firstSwing > 800) { swings = 0; firstSwing = t; }
    if (++swings >= 3) { swings = 0; stopSound = true; flash("OK", "dismissed"); Serial.println("IMU shake -> dismiss"); }
  }
}

// ---------- WS2812B chain over RMT ----------
const int NPIX = 3;               // chain order on the wire (v6, 2026-10-29): 0 REC (left eye), 1 HEAD (right eye), 2 SKIRT (right foot)
const int PIX_REC = 0, PIX_HEAD = 1, PIX_SKIRT = 2;
const uint8_t PIX_MAX = 64;       // 25% brightness cap
uint8_t pix[NPIX][3];             // r, g, b
rmt_data_t pixBits[NPIX * 24];
void pixSet(int i, uint8_t r, uint8_t g, uint8_t b) {
  pix[i][0] = (uint16_t)r * PIX_MAX / 255; pix[i][1] = (uint16_t)g * PIX_MAX / 255; pix[i][2] = (uint16_t)b * PIX_MAX / 255;
}
void pixShow() {
  if (!pixOK) return;
  int k = 0;
  for (int i = 0; i < NPIX; i++) {
    uint8_t grb[3] = {pix[i][1], pix[i][0], pix[i][2]};     // WS2812B wants G, R, B
    for (int c = 0; c < 3; c++)
      for (int bit = 7; bit >= 0; bit--) {
        bool one = grb[c] & (1 << bit);                      // 10 MHz ticks: 0 = 0.4/0.85 us, 1 = 0.8/0.45 us
        pixBits[k].level0 = 1; pixBits[k].duration0 = one ? 8 : 4;
        pixBits[k].level1 = 0; pixBits[k].duration1 = one ? 4 : 8;
        k++;
      }
  }
  rmtWrite(PIN_PIX, pixBits, k, RMT_WAIT_FOR_EVER);
}
void pixOff() { for (int i = 0; i < NPIX; i++) pixSet(i, 0, 0, 0); pixShow(); }

float breath(uint32_t periodMs) { return 0.5f - 0.5f * cosf(2 * PI * (millis() % periodMs) / periodMs); }

void updatePixels() {
  uint32_t t = millis();
  for (int i = 0; i < NPIX; i++) pixSet(i, 0, 0, 0);
  if (!privateMode) {
    // REC is firmware-only: solid red = camera on, pulsing red = mic only, one flash = photo
    if (rec == REC_CLIP || rec == REC_VIDEO) pixSet(0, 255, 0, 0);
    else if (rec == REC_AUDIO || talking) pixSet(0, (uint8_t)(255 * (0.2f + 0.8f * breath(1200))), 0, 0);
    else if (t < photoFlashUntil) pixSet(0, 255, 0, 0);
    // HEAD: cyan while listening, dim while recording, a slow violet breath otherwise (Ghost's colour TBD)
    if (talking) pixSet(PIX_HEAD, 0, 200, 255);
    else if (rec != REC_NONE) pixSet(PIX_HEAD, 20, 0, 40);
    else { float b = 0.15f + 0.85f * breath(4000); pixSet(PIX_HEAD, (uint8_t)(140 * b), (uint8_t)(60 * b), (uint8_t)(255 * b)); }
    // SKIRT: approval waiting, approved, refused, frozen
    if (t < approvedUntil) pixSet(PIX_SKIRT, 0, 255, 0);
    else if (t < refusedUntil) pixSet(PIX_SKIRT, 80, 0, 0);
    else if (frozen) pixSet(PIX_SKIRT, (t / 150) % 2 ? 120 : 0, 0, 0);
    else if (askPending) pixSet(PIX_SKIRT, (uint8_t)(255 * breath(1500)), (uint8_t)(120 * breath(1500)), 0);
    else if (batMv && batMv < 3550 && (t % 10000) < 120) pixSet(PIX_SKIRT, 255, 0, 0);   // under ~15%
  }
  static uint32_t lastShow = 0;
  if (t - lastShow >= 30) { lastShow = t; pixShow(); }
}

// ---------- the shared bus: microSD (SPI) or the amp (I2S) on D8-D10, never both ----------
void busRelease() {
  if (bus == BUS_AUDIO) { spk.end(); digitalWrite(PIN_AMP_SD, LOW); }
  if (bus == BUS_STORAGE) { SD.end(); SPI.end(); }
  digitalWrite(PIN_SD_CS, HIGH);
  bus = BUS_IDLE;
}
bool busTake(Bus want) {
  if (bus == want) return true;
  busRelease();
  if (want == BUS_STORAGE) {
    SPI.begin(PIN_BUS_SCK, PIN_BUS_MISO, PIN_BUS_MOSI, PIN_SD_CS);
    bool mounted = false;
    for (uint32_t hz : {20000000UL, 4000000UL, 1000000UL}) {   // slower clocks forgive a marginal slot contact
      if (SD.begin(PIN_SD_CS, SPI, hz)) { mounted = true; sdHz = hz; break; }
      SD.end();
    }
    if (!mounted) { SPI.end(); return false; }
  } else if (want == BUS_AUDIO) {
    if (!ampFitted) return false;
    spk.setPins(PIN_BUS_SCK, PIN_BUS_MISO, PIN_BUS_MOSI);
    if (!spk.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) return false;
    digitalWrite(PIN_AMP_SD, HIGH);
  }
  bus = want;
  return true;
}

// ---------- sound (no-ops until "amp on") ----------
void tone16k(float hz, uint32_t ms, float vol = 0.3f) {
  if (!ampFitted || !busTake(BUS_AUDIO)) return;
  static int16_t buf[256];
  uint32_t n = 16 * ms; float ph = 0, step = 2 * PI * hz / 16000.0f;
  stopSound = false;
  while (n && !stopSound) {
    int m = min<uint32_t>(n, 256);
    for (int i = 0; i < m; i++) { buf[i] = (int16_t)(sinf(ph) * 32767 * vol); ph += step; }
    spk.write((uint8_t*)buf, m * 2);
    n -= m;
  }
  busRelease();
}
void chirp() { tone16k(660, 80); tone16k(990, 120); }

// ---------- mic ----------
void micPoll() {
  if (!micOK || privateMode) { micRms = micPeak = 0; delay(20); return; }
  const int N = 800;                                // 50 ms at 16 kHz
  static int16_t buf[N];
  size_t got = mic.readBytes((char*)buf, sizeof(buf)) / 2;
  double sum = 0; int peak = 0;
  for (size_t i = 0; i < got; i++) { int v = buf[i]; sum += (double)v * v; if (abs(v) > peak) peak = abs(v); }
  micRms = got ? (int)sqrt(sum / got) : 0;
  micPeak = peak;
}

// ---------- battery ----------
void batPoll() {
  static uint32_t last = 0;
  if (last && millis() - last < 2000) return;
  last = millis();
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) sum += analogReadMilliVolts(PIN_BAT);
  int mv = sum / 16 * 2;                             // divider halves the cell voltage
  batMv = mv < 1000 ? 0 : mv;                        // under 1 V: no cell (or nothing wired)
}
int batPct() { return batMv ? constrain((batMv - 3300) * 100 / 900, 0, 100) : -1; }   // 3.3 V = 0%, 4.2 V = 100%, linear

// ---------- OLED ----------
const char* recName() { return rec == REC_CLIP ? "CLIP" : rec == REC_VIDEO ? "VIDEO" : rec == REC_AUDIO ? "AUDIO" : ""; }

void drawHeader(const char* title) {
  oled.setFont(u8g2_font_6x10_tf);
  oled.drawStr(0, 9, title);
  if (rec != REC_NONE) { if ((millis() / 500) % 2) oled.drawDisc(92, 5, 3); oled.drawStr(98, 9, recName()); }
  else if (talking) oled.drawStr(98, 9, "ear");
  oled.drawHLine(0, 11, 128);
}
void drawMicBar(int y, int h) {
  int w = constrain(micRms / 20, 0, 128);
  oled.drawFrame(0, y, 128, h);
  if (w > 2) oled.drawBox(1, y + 1, w - 2, h - 2);
}
void drawBig(const char* a, const char* b) {
  oled.setFont(u8g2_font_10x20_tf);
  oled.drawStr(max(0, 64 - 5 * (int)strlen(a)), 34, a);
  oled.setFont(u8g2_font_6x10_tf);
  oled.drawStr(max(0, 64 - 3 * (int)strlen(b)), 52, b);
}

bool offChord = false; uint32_t offChordAt = 0;

void drawScreen() {
  if (!oledOK) return;
  oled.clearBuffer();
  char line[34];
  if (offChord) {
    char c[24]; snprintf(c, sizeof c, "off in %lu", (unsigned long)((3000 - min(3000UL, millis() - offChordAt)) / 1000 + 1));
    drawBig("POWER", c);
  } else if (frozen) drawBig("FROZEN", "hold ACT 3 s to clear");
  else if (privateMode) drawBig("PRIVATE", "mic off  cam off");
  else if (talking) {
    drawHeader("LISTENING");
    oled.setFont(u8g2_font_10x20_tf);
    oled.drawStr(4, 36, "say it...");
    drawMicBar(44, 16);
  } else if (millis() < flashUntil) drawBig(flashText, flashSub);
  else if (askPending) drawBig("APPROVE?", "hold ACT 1 s / tap = no");
  else switch (screen) {
    case 0:
      drawHeader("GHOST");
      snprintf(line, sizeof line, "i2c %s", i2cFound.c_str());
      oled.drawStr(0, 23, line);
      snprintf(line, sizeof line, "oled%d imu%d mic%d sd%d amp%d", oledOK, imuOK, micOK, sdOK, ampFitted);
      oled.drawStr(0, 35, line);
      snprintf(line, sizeof line, "up %lus  photos %lu", (unsigned long)(millis() / 1000), (unsigned long)photos);
      oled.drawStr(0, 47, line);
      drawMicBar(52, 10);
      break;
    case 1:
      drawHeader("LAST ANSWER");
      oled.drawStr(0, 30, "(no brain on the bench)");
      oled.drawStr(0, 44, "TALK tap = 440 Hz tone");
      break;
    case 2:
      drawHeader("MOTION (IMU)");
      if (imuOK) {
        snprintf(line, sizeof line, "pitch %5.0f  roll %5.0f", pitchDeg, rollDeg); oled.drawStr(0, 25, line);
        snprintf(line, sizeof line, "|a| %.2f g  %s", aMag, faceDown ? "face down" : still ? "still" : "moving"); oled.drawStr(0, 39, line);
        snprintf(line, sizeof line, "int on ACT: %s", imuIntOnAct ? "on" : "off"); oled.drawStr(0, 53, line);
      } else oled.drawStr(0, 30, "IMU not found");
      break;
    case 3:
      drawHeader("MIC");
      snprintf(line, sizeof line, "rms %d  peak %d", micRms, micPeak); oled.drawStr(0, 25, line);
      drawMicBar(32, 24);
      oled.drawStr(0, 63, micOK ? "clap: the bar jumps" : "mic not started");
      break;
    case 4:
      drawHeader("BATTERY + PARTS");
      if (batMv) snprintf(line, sizeof line, "%d.%02d V  %d%%", batMv / 1000, (batMv % 1000) / 10, batPct());
      else snprintf(line, sizeof line, "no cell");
      oled.drawStr(0, 25, line);
      snprintf(line, sizeof line, "sd %s  amp %s", sdOK ? "card" : "none", ampFitted ? "fitted" : "not yet"); oled.drawStr(0, 39, line);
      snprintf(line, sizeof line, "pix chain %d  wake %s", NPIX, lastWake); oled.drawStr(0, 53, line);
      break;
  }
  oled.sendBuffer();
}

// ---------- power off and wake ----------
void sleepNow(bool armImu) {
  pixOff();
  if (oledOK) { oled.clearBuffer(); oled.setFont(u8g2_font_10x20_tf); oled.drawStr(34, 38, armImu ? "zzz" : "bye"); oled.sendBuffer(); delay(500); oled.setPowerSave(1); }
  busRelease();
  if (micOK) mic.end();
  if (imuOK) {
    if (armImu && imuIntOnAct) { imuWrite(IMU_CTRL1, 0x01); imuIntConfig(true); imuWokeUp(); }   // low ODR, wake armed
    else { imuIntConfig(false); imuWrite(IMU_CTRL1, 0x00); }                                     // power down
  }
  while (digitalRead(PIN_BTN_TALK) == LOW || digitalRead(PIN_BTN_CAPTURE) == LOW || digitalRead(PIN_BTN_ACT) == LOW) delay(10);
  delay(100);
  uint64_t mask = 0;
  for (int p : {PIN_BTN_TALK, PIN_BTN_CAPTURE, PIN_BTN_ACT}) {
    rtc_gpio_pullup_en((gpio_num_t)p); rtc_gpio_pulldown_dis((gpio_num_t)p);
    mask |= 1ULL << p;
  }
  if (!armImu) mask = 1ULL << PIN_BTN_TALK;          // powered off: only TALK (held 1.5 s) brings it back
  Serial.printf("%s -> deep sleep\n", armImu ? "NAP" : "POWER OFF");
  Serial.flush();
  esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW);
  prefs.putBool("off", !armImu);
  esp_deep_sleep_start();
}

// After a power off, TALK must be held 1.5 s or it goes straight back to sleep (a pocket can't power it on).
void confirmWake() {
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_EXT1) return;
  uint64_t st = esp_sleep_get_ext1_wakeup_status();
  lastWake = st & (1ULL << PIN_BTN_TALK) ? "TALK" : st & (1ULL << PIN_BTN_CAPTURE) ? "CAPTURE" : "ACT/IMU";
  if (!prefs.getBool("off", false)) return;          // a nap: any button or a pickup wakes it
  pinMode(PIN_BTN_TALK, INPUT_PULLUP);
  uint32_t t0 = millis();
  while (digitalRead(PIN_BTN_TALK) == LOW) {
    if (millis() - t0 >= 1500) { prefs.putBool("off", false); return; }
    delay(10);
  }
  rtc_gpio_pullup_en((gpio_num_t)PIN_BTN_TALK); rtc_gpio_pulldown_dis((gpio_num_t)PIN_BTN_TALK);
  esp_sleep_enable_ext1_wakeup(1ULL << PIN_BTN_TALK, ESP_EXT1_WAKEUP_ANY_LOW);
  esp_deep_sleep_start();
}

// ---------- serial commands ----------
void i2cScan() {
  i2cFound = "";
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      char h[6]; snprintf(h, sizeof h, "%02X ", a);
      i2cFound += h;
      const char* what = a == 0x3C ? "OLED" : (a == 0x6A || a == 0x6B) ? "IMU" : a == 0x36 ? "fuel gauge" : "?";
      Serial.printf("  0x%02X  %s\n", a, what);
    }
  }
  if (i2cFound.isEmpty()) Serial.println("  nothing on the bus: check 3V3/GND and SDA=D4, SCL=D5");
}

void printResult() {
  char bat[12];
  if (batMv) snprintf(bat, sizeof bat, "%d.%02d", batMv / 1000, (batMv % 1000) / 10); else strcpy(bat, "none");
  Serial.printf("RESULT oled=%d imu=%d amp=%d mic=%d sd=%d pix=%d bat=%s i2c=[%s]\n",
                oledOK, imuOK, ampFitted, micOK, sdOK, pixOK ? NPIX : 0, bat, i2cFound.c_str());
}

// 100 cycles of: a 1 s tone, a write + read-back on the card, a 1 s tone. Proves the SD and the amp can share D8-D10.
void busTest(int cycles, int mb) {
  if (!ampFitted) { Serial.println("bustest: the amp isn't fitted (\"amp on\" once it's wired)"); return; }
  if (!sdOK) { Serial.println("bustest: no microSD found at boot (FAT32 card in the Sense slot, then reset)"); return; }
  Serial.printf("bustest: %d cycles, %d MB each\n", cycles, mb);
  static uint8_t buf[4096];
  int errors = 0;
  uint32_t t0 = millis();
  for (int c = 1; c <= cycles; c++) {
    tone16k(440, 1000);
    if (!busTake(BUS_STORAGE)) { Serial.printf("  cycle %d: card didn't come back\n", c); errors++; continue; }
    File f = SD.open("/bustest.bin", FILE_WRITE);
    if (!f) { Serial.printf("  cycle %d: open for write failed\n", c); errors++; busRelease(); continue; }
    for (int i = 0; i < mb * 256; i++) { memset(buf, (uint8_t)(i + c), sizeof buf); if (f.write(buf, sizeof buf) != sizeof buf) { errors++; break; } }
    f.close();
    f = SD.open("/bustest.bin", FILE_READ);
    for (int i = 0; f && i < mb * 256; i++) {
      if (f.read(buf, sizeof buf) != sizeof buf || buf[0] != (uint8_t)(i + c) || buf[4095] != (uint8_t)(i + c)) { errors++; Serial.printf("  cycle %d: read-back mismatch at block %d\n", c, i); break; }
    }
    if (f) f.close();
    busRelease();
    tone16k(660, 1000);
    Serial.printf("  cycle %d/%d ok, errors so far %d\n", c, cycles, errors);
  }
  if (busTake(BUS_STORAGE)) { SD.remove("/bustest.bin"); busRelease(); }
  Serial.printf("BUSTEST cycles=%d errors=%d seconds=%lu\n", cycles, errors, (unsigned long)((millis() - t0) / 1000));
}

void printHelp() {
  Serial.println("commands: help | status | scan | amp on|off | imuint on|off | sd | tone | ask | nap | bustest [cycles] [MB]");
}

void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n'); cmd.trim();
  if (cmd == "help") printHelp();
  else if (cmd == "status") printResult();
  else if (cmd == "scan") i2cScan();
  else if (cmd == "amp on" || cmd == "amp off") { ampFitted = cmd == "amp on"; prefs.putBool("amp", ampFitted); Serial.printf("amp %s (saved)\n", ampFitted ? "fitted" : "not fitted"); if (ampFitted) chirp(); }
  else if (cmd == "imuint on" || cmd == "imuint off") { imuIntOnAct = cmd == "imuint on"; prefs.putBool("imuint", imuIntOnAct); imuIntConfig(imuIntOnAct); Serial.printf("IMU INT1 on ACT %s (saved)\n", imuIntOnAct ? "on" : "off"); }
  else if (cmd == "sd") {                          // remount without a reboot, after reseating the card
    busRelease(); sdOK = busTake(BUS_STORAGE);
    if (sdOK) Serial.printf("sd: ok, type %d, %llu MB at %lu MHz\n", (int)SD.cardType(), SD.cardSize() / (1024ULL * 1024ULL), (unsigned long)(sdHz / 1000000));
    else Serial.println("sd: no card at 20, 4 or 1 MHz");
    busRelease();
  }
  else if (cmd == "tone") tone16k(440, 500);
  else if (cmd == "ask") { askPending = true; Serial.println("test request on screen: hold ACT 1 s = approve, tap = no"); }
  else if (cmd == "nap") sleepNow(true);
  else if (cmd.startsWith("bustest")) {
    int cycles = 100, mb = 10;
    sscanf(cmd.c_str(), "bustest %d %d", &cycles, &mb);
    busTest(max(1, cycles), max(1, mb));
  } else if (cmd.length()) { Serial.printf("unknown: %s\n", cmd.c_str()); printHelp(); }
}

// ---------- setup ----------
void setup() {
  // The chain first: GPIO43 carried the boot log, so the pixels may be showing garbage.
  pixOK = rmtInit(PIN_PIX, RMT_TX_MODE, RMT_MEM_NUM_BLOCKS_1, 10000000);
  pixOff();
  pinMode(PIN_AMP_SD, OUTPUT); digitalWrite(PIN_AMP_SD, LOW);   // amp off and deaf to SD traffic
  pinMode(PIN_SD_CS, OUTPUT); digitalWrite(PIN_SD_CS, HIGH);    // card deselected
  prefs.begin("ghost", false);
  ampFitted = prefs.getBool("amp", false);
  imuIntOnAct = prefs.getBool("imuint", false);

  Serial.begin(115200);
  delay(2000);
  Serial.println("\n==== companion-bench: Ghost on the breadboard (XIAO ESP32S3 Sense) ====");
  confirmWake();

  for (int i = 0; i < NPIX; i++) {                  // self-test: REC red, HEAD violet, SKIRT amber, in turn
    pixOff();
    if (i == PIX_REC) pixSet(PIX_REC, 255, 0, 0); else if (i == PIX_SKIRT) pixSet(PIX_SKIRT, 255, 120, 0); else pixSet(PIX_HEAD, 140, 60, 255);
    pixShow(); delay(250);
  }
  pixOff();
  bTalk.begin(PIN_BTN_TALK, "TALK"); bCapture.begin(PIN_BTN_CAPTURE, "CAPTURE"); bAct.begin(PIN_BTN_ACT, "ACT");
  analogSetPinAttenuation(PIN_BAT, ADC_11db);

  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Serial.println("[1] I2C scan (expect 0x3C OLED, 0x6A/0x6B IMU)");
  i2cScan();

  Serial.println("[2] OLED");
  oled.setI2CAddress(0x3C << 1);
  oledOK = oled.begin();
  Serial.println(oledOK ? "  ok" : "  u8g2 begin failed");
  if (oledOK) { oled.clearBuffer(); oled.setFont(u8g2_font_10x20_tf); oled.drawStr(34, 38, "GHOST"); oled.sendBuffer(); }

  Serial.println("[3] IMU LSM6DSV16X");
  imuOK = imuBegin();
  Serial.println(imuOK ? "  ok" : "  not found (0x6A/0x6B, or WHO_AM_I wrong; if absent, tie the CS pad to 3V3)");
  if (imuOK) imuIntConfig(imuIntOnAct);

  // The card starts in SPI mode before the amp's I2S ever clocks these pins (a card that isn't started yet
  // listens in SD mode and could take the I2S traffic as commands).
  Serial.println("[4] microSD (SPI on D8-D10, CS 21)");
  sdOK = busTake(BUS_STORAGE);
  if (sdOK) Serial.printf("  ok, %llu MB card at %lu MHz\n", SD.cardSize() / (1024ULL * 1024ULL), (unsigned long)(sdHz / 1000000));
  else Serial.println("  no card (FAT32 microSD in the Sense slot, then reset)");
  busRelease();

  Serial.println("[5] PDM mic (CLK 42, DATA 41)");
  mic.setPinsPdmRx(42, 41);
  micOK = mic.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  Serial.println(micOK ? "  ok" : "  I2S PDM driver failed to start");

  Serial.printf("[6] amp %s, IMU INT1 on ACT %s (serial: amp on|off, imuint on|off)\n", ampFitted ? "fitted" : "not fitted", imuIntOnAct ? "on" : "off");
  if (ampFitted) chirp();

  batPoll();
  printResult();
  Serial.println("TALK hold=listen tap=repeat | CAPTURE tap=photo hold=clip x2=video x3=audio | ACT tap=home x2=next hold1s=approve hold3s=freeze | TALK+ACT 1s=private | all three 3s=off");
  printHelp();
}

// ---------- loop ----------
bool privChord = false; uint32_t privChordAt = 0; bool privFired = false;

void loop() {
  bool rawTalk = digitalRead(PIN_BTN_TALK) == LOW, rawCap = digitalRead(PIN_BTN_CAPTURE) == LOW, rawAct = digitalRead(PIN_BTN_ACT) == LOW;
  // With the IMU on the ACT line, a low that the IMU caused is motion, not a press.
  static bool lastRawAct = false;
  if (imuIntOnAct && rawAct && !lastRawAct && imuWokeUp()) { Serial.println("ACT line low: wake=motion (not a press)"); rawAct = false; }
  if (imuIntOnAct && !rawAct && lastRawAct) imuWokeUp();
  lastRawAct = digitalRead(PIN_BTN_ACT) == LOW;
  bTalk.poll(rawTalk); bCapture.poll(rawCap); bAct.poll(rawAct);

  // All three held 3 s: power off. Their own actions are suppressed while the chord is down.
  if (bTalk.down && bCapture.down && bAct.down) {
    if (!offChord) { offChord = true; offChordAt = millis(); talking = false; }
    bTalk.suppress = bCapture.suppress = bAct.suppress = true;
    if (millis() - offChordAt >= 3000) sleepNow(false);    // never returns
  } else if (offChord && !bTalk.down && !bCapture.down && !bAct.down) offChord = false;

  // TALK + ACT held 1 s: PRIVATE on/off
  if (!offChord && bTalk.down && bAct.down && !bCapture.down) {
    if (!privChord) { privChord = true; privChordAt = millis(); privFired = false; talking = false; }
    bTalk.suppress = bAct.suppress = true;
    if (!privFired && millis() - privChordAt >= 1000) { privFired = true; privateMode = !privateMode; rec = REC_NONE; Serial.printf("TALK+ACT -> %s\n", privateMode ? "PRIVATE" : "normal"); }
  } else if (privChord && !bTalk.down && !bAct.down) privChord = false;

  // ACT: freeze wins over everything; hold 3 s toggles it
  if (bAct.down && !bAct.suppress && bAct.heldFor() >= 3000) {
    bAct.suppress = true; frozen = !frozen; askPending = false; rec = REC_NONE; talking = false;
    Serial.printf("ACT hold 3 s -> %s\n", frozen ? "FREEZE" : "unfrozen");
  }
  if (!frozen && !privateMode && !offChord && !privChord) {
    // ACT
    if (bAct.down && !bAct.suppress && askPending && bAct.heldFor() >= 1000) {
      bAct.suppress = true; askPending = false; approvals++; approvedUntil = millis() + 800;
      char s[24]; snprintf(s, sizeof s, "#%lu", (unsigned long)approvals); flash("APPROVED", s); tone16k(880, 120);
      Serial.printf("ACT hold 1 s -> approved #%lu\n", (unsigned long)approvals);
    }
    if (bAct.clicks == 1) {
      if (askPending) { askPending = false; refusedUntil = millis() + 600; flash("NO", "refused"); Serial.println("ACT tap -> no"); }
      else { screen = 0; stopSound = true; Serial.println("ACT tap -> home"); }
    }
    if (bAct.clicks >= 2) { screen = (screen + 1) % SCREENS; Serial.printf("ACT double -> screen %d\n", screen); }

    // CAPTURE
    if (bCapture.holdStart && rec == REC_NONE) { rec = REC_CLIP; Serial.println("CAPTURE hold -> clip while held (no camera on the bench)"); }
    if (bCapture.holdEnd && rec == REC_CLIP) { rec = REC_NONE; Serial.println("CAPTURE released -> clip ends"); }
    if (bCapture.clicks) {
      if (rec == REC_VIDEO || rec == REC_AUDIO) { Serial.printf("CAPTURE tap -> %s stops\n", recName()); rec = REC_NONE; }
      else if (bCapture.clicks == 1) { photos++; photoFlashUntil = millis() + 150; char s[24]; snprintf(s, sizeof s, "#%lu", (unsigned long)photos); flash("PHOTO", s); Serial.printf("CAPTURE tap -> photo #%lu\n", (unsigned long)photos); }
      else if (bCapture.clicks == 2) { rec = REC_VIDEO; Serial.println("CAPTURE double -> video on (tap to stop)"); }
      else { rec = REC_AUDIO; Serial.println("CAPTURE triple -> audio on (tap to stop)"); }
    }

    // TALK
    if (bTalk.holdStart) { talking = true; Serial.println("TALK hold -> listening"); }
    if (bTalk.holdEnd && talking) { talking = false; Serial.println("TALK released -> sent"); }
    if (bTalk.clicks) { Serial.println("TALK tap -> repeat last answer (tone)"); flash("REPEAT", ampFitted ? "440 Hz" : "amp not fitted"); tone16k(440, 400); }
  } else talking = false;

  if (imuOK) { imuPoll(); motionPoll(); }
  micPoll();                                        // 50 ms of audio; this paces the loop
  batPoll();
  handleSerial();

  static uint32_t lastDraw = 0;
  if (millis() - lastDraw > 100) { lastDraw = millis(); drawScreen(); }
  updatePixels();

  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 2000) {
    lastPrint = millis();
    Serial.printf("scr %d%s%s%s%s | pitch %4.0f roll %4.0f |a| %.2f %s | mic rms %4d peak %5d%s | bat %s%d mV\n",
                  screen, talking ? " TALK" : "", rec != REC_NONE ? " REC:" : "", recName(), privateMode ? " PRIVATE" : "",
                  pitchDeg, rollDeg, aMag, faceDown ? "down" : still ? "still" : "moving",
                  micRms, micPeak, micRms > 300 ? " <-- sound" : "", batMv ? "" : "none ", batMv);
  }
}

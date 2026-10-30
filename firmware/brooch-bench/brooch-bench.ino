// brooch-bench.ino: bench test for the Exo brooch v0 on a breadboard, no camera, no Wi-Fi, no secrets.
// XIAO ESP32S3 Sense + 0.96" OLED (SSD1315, 0x3C) + LSM6DSV16X IMU (0x6A/0x6B) + BME680 (0x77)
// + 3 buttons + 2 LEDs. Board XIAO_ESP32S3, PSRAM = OPI PSRAM, esp32 core 3.x. Flash with flash.sh.
//
// Wiring (vault: Areas/argo/argo-exo/current/brooch/exo-brooch-bench-build-guide.md, controls in
// exo-brooch-controls.md):
//   D4 (GPIO5) SDA, D5 (GPIO6) SCL, everything on 3V3.
//   MARK    D0 (GPIO1) to GND: short = mark a moment, hold 0.7 s = REC on/off.
//   TALK    D1 (GPIO2) to GND: tap = next screen, hold = push-to-talk (LISTENING while held).
//   APPROVE D2 (GPIO3) to GND: short = approve, hold 2 s = FREEZE on/off.
//   MARK + TALK held together 1 s = PRIVATE on/off.
//   MARK + APPROVE held together 3 s = POWER OFF (deep sleep, ~10 uA + peripherals). Hold APPROVE 1.5 s to power on.
//   D3 (GPIO4, ADC) is free: battery divider later.
//   STATUS LED D6 (GPIO43) -> resistor -> LED -> GND.   REC LED (red) D7 (GPIO44) -> resistor -> LED -> GND.
//   PDM mic on the Sense board: CLK 42, DATA 41.
// Serial 115200: an I2C scan at boot, then one status line every 2 s and one line per button event.
#include <Wire.h>
#include <U8g2lib.h>
#include <Adafruit_BME680.h>
#include "ESP_I2S.h"
#include <math.h>
#include "driver/rtc_io.h"
#include "esp_sleep.h"

// ---------- pins ----------
const int PIN_BTN_MARK    = D0;   // GPIO1
const int PIN_BTN_TALK    = D1;   // GPIO2
const int PIN_BTN_APPROVE = D2;   // GPIO3
const int PIN_SPARE       = D3;   // GPIO4, ADC-capable, unused in v1
const int PIN_SDA = D4;           // GPIO5
const int PIN_SCL = D5;           // GPIO6
const int PIN_LED_STATUS = D6;    // GPIO43 (UART TX: flickers for a moment at boot, harmless)
const int PIN_LED_REC    = D7;    // GPIO44
const int PIN_LED_XIAO   = LED_BUILTIN;   // yellow user LED on the XIAO, active LOW; hidden inside the case later

// ---------- devices ----------
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE);
Adafruit_BME680 bme(&Wire);
I2SClass i2s;

bool oledOK = false, bmeOK = false, imuOK = false, micOK = false;
uint8_t imuAddr = 0;
String i2cFound;

// ---------- state ----------
enum Mode { LIFELOG, REC, PRIVATE };
Mode mode = LIFELOG;
bool talking = false, frozen = false;
int screen = 0;
const int SCREENS = 4;
uint32_t flashUntil = 0;              // a short full-screen message
const char* flashText = "";
char flashSub[24] = "";
uint32_t marks = 0, approvals = 0;

// sensor readings
float temperature = NAN, humidity = NAN, pressure = NAN, gasKohm = NAN;
float ax = 0, ay = 0, az = 0, pitchDeg = 0, rollDeg = 0, aMag = 1;
uint32_t steps = 0;
int micRms = 0, micPeak = 0;

// ---------- buttons: short press on release, long press after longMs, held while down ----------
struct Button {
  int pin; uint32_t longMs;
  bool down = false, longFired = false;
  uint32_t downAt = 0, lastChange = 0;
  bool shortPress = false, longPress = false;
  void begin(int p, uint32_t lm) { pin = p; longMs = lm; pinMode(pin, INPUT_PULLUP); }
  void poll() {
    shortPress = longPress = false;
    bool now = digitalRead(pin) == LOW;
    uint32_t t = millis();
    if (now != down && t - lastChange > 30) {        // debounce
      down = now; lastChange = t;
      if (down) { downAt = t; longFired = false; }
      else if (!longFired) shortPress = true;
    }
    if (down && !longFired && t - downAt >= longMs) { longFired = true; longPress = true; }
  }
};
Button bMark, bTalk, bApprove;
bool chord = false; uint32_t chordAt = 0; bool chordFired = false;   // MARK + TALK together = PRIVATE
bool offChord = false; uint32_t offChordAt = 0;                      // MARK + APPROVE together 3 s = power off

void flash(const char* text, const char* sub = "") {
  flashText = text; strncpy(flashSub, sub, sizeof flashSub - 1); flashUntil = millis() + 1200;
}

// ---------- LSM6DSV16X over raw registers (no library) ----------
const uint8_t IMU_WHO_AM_I = 0x0F, IMU_CTRL1 = 0x10, IMU_CTRL2 = 0x11, IMU_CTRL3 = 0x12, IMU_OUTX_L_A = 0x28;

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
bool imuBegin() {
  for (uint8_t a : {0x6A, 0x6B}) {
    imuAddr = a;
    uint8_t who = 0;
    if (imuRead(IMU_WHO_AM_I, &who, 1) == 1) {
      Serial.printf("  IMU at 0x%02X WHO_AM_I=0x%02X (LSM6DSV16X is 0x70)\n", a, who);
      if (who == 0x70) break;
    }
    imuAddr = 0;
  }
  if (!imuAddr) return false;
  imuWrite(IMU_CTRL3, 0x01); delay(20);           // software reset
  imuWrite(IMU_CTRL3, 0x44);                      // BDU + register auto-increment
  imuWrite(IMU_CTRL1, 0x05);                      // accel 60 Hz, high performance, +-2 g (default FS)
  imuWrite(IMU_CTRL2, 0x05);                      // gyro 60 Hz (unused here)
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
  static bool above = false; static uint32_t lastStep = 0;   // crude step detector
  if (!above && aMag > 1.25f && millis() - lastStep > 300) { above = true; lastStep = millis(); steps++; }
  if (above && aMag < 1.05f) above = false;
}

// ---------- mic ----------
void micPoll() {
  if (!micOK || mode == PRIVATE) { micRms = micPeak = 0; return; }
  const int N = 800;                                // 50 ms at 16 kHz
  static int16_t buf[N];
  size_t got = i2s.readBytes((char*)buf, sizeof(buf)) / 2;
  double sum = 0; int peak = 0;
  for (size_t i = 0; i < got; i++) { int v = buf[i]; sum += (double)v * v; if (abs(v) > peak) peak = abs(v); }
  micRms = got ? (int)sqrt(sum / got) : 0;
  micPeak = peak;
}

// ---------- OLED ----------
const char* modeName() { return frozen ? "FROZEN" : mode == REC ? "REC" : mode == PRIVATE ? "OFF" : "LIFELOG"; }

void drawHeader(const char* title) {
  oled.setFont(u8g2_font_6x10_tf);
  oled.drawStr(0, 9, title);
  if (mode == REC) {
    if ((millis() / 500) % 2) oled.drawDisc(98, 5, 3);
    oled.drawStr(104, 9, "REC");
  } else if (mode == PRIVATE) oled.drawStr(104, 9, "OFF");
  if (talking) oled.drawStr(72, 9, "ear");
  oled.drawHLine(0, 11, 128);
}

void drawMicBar(int y, int h) {
  int w = constrain(micRms / 20, 0, 128);
  oled.drawFrame(0, y, 128, h);
  if (w > 2) oled.drawBox(1, y + 1, w - 2, h - 2);
}

void drawBig(const char* a, const char* b) {
  oled.setFont(u8g2_font_10x20_tf);
  oled.drawStr(64 - 5 * strlen(a), 34, a);
  oled.setFont(u8g2_font_6x10_tf);
  oled.drawStr(64 - 3 * strlen(b), 52, b);
}

void drawScreen() {
  if (!oledOK) return;
  oled.clearBuffer();
  char line[32];
  if (offChord) {
    char c[24]; snprintf(c, sizeof c, "off in %lu", (unsigned long)((3000 - min(3000UL, millis() - offChordAt)) / 1000 + 1));
    drawBig("POWER", c);
  } else if (frozen) {
    drawBig("FROZEN", "hold APPROVE 2 s to clear");
  } else if (mode == PRIVATE) {
    drawBig("PRIVATE", "mic off cam off  MARK+TALK");
  } else if (talking) {
    drawHeader("LISTENING");
    oled.setFont(u8g2_font_10x20_tf);
    oled.drawStr(4, 36, "say it...");
    drawMicBar(44, 16);
  } else if (millis() < flashUntil) {
    drawBig(flashText, flashSub);
  } else switch (screen) {
    case 0:
      drawHeader("EXO BROOCH");
      snprintf(line, sizeof line, "i2c %s", i2cFound.c_str());
      oled.drawStr(0, 23, line);
      snprintf(line, sizeof line, "oled%d imu%d air%d mic%d", oledOK, imuOK, bmeOK, micOK);
      oled.drawStr(0, 35, line);
      snprintf(line, sizeof line, "up %lus  mode %s", (unsigned long)(millis() / 1000), modeName());
      oled.drawStr(0, 47, line);
      drawMicBar(52, 10);
      break;
    case 1:
      drawHeader("AIR  (BME680)");
      if (bmeOK) {
        snprintf(line, sizeof line, "%.1f C   %.0f %%RH", temperature, humidity);
        oled.drawStr(0, 25, line);
        snprintf(line, sizeof line, "%.1f hPa", pressure);
        oled.drawStr(0, 39, line);
        snprintf(line, sizeof line, "gas %.0f kOhm", gasKohm);
        oled.drawStr(0, 53, line);
      } else oled.drawStr(0, 30, "BME680 not found");
      break;
    case 2:
      drawHeader("MOTION (IMU)");
      if (imuOK) {
        snprintf(line, sizeof line, "pitch %5.0f  roll %5.0f", pitchDeg, rollDeg);
        oled.drawStr(0, 25, line);
        snprintf(line, sizeof line, "|a| %.2f g", aMag);
        oled.drawStr(0, 39, line);
        snprintf(line, sizeof line, "steps %lu", (unsigned long)steps);
        oled.drawStr(0, 53, line);
      } else oled.drawStr(0, 30, "IMU not found");
      break;
    case 3:
      drawHeader("MIC");
      snprintf(line, sizeof line, "rms %d  peak %d", micRms, micPeak);
      oled.drawStr(0, 25, line);
      drawMicBar(32, 24);
      oled.drawStr(0, 63, micOK ? "clap: the bar jumps" : "mic not started");
      break;
  }
  oled.sendBuffer();
}

// ---------- LEDs ----------
void updateLeds() {
  uint32_t t = millis();
  // REC LED: solid while recording, a 150 ms blink on MARK, off otherwise (and always off in PRIVATE)
  bool rec = mode == REC || (t < flashUntil && flashText[0] == 'M' && (flashUntil - t) > 1050);
  digitalWrite(PIN_LED_REC, rec && mode != PRIVATE);
  // STATUS LED: off in PRIVATE; fast blink when FROZEN; solid while TALK; 60 ms heartbeat every 3 s otherwise
  bool st;
  if (mode == PRIVATE) st = false;
  else if (frozen) st = (t / 150) % 2;
  else if (talking) st = true;
  else if (t < flashUntil && flashText[0] == 'A') st = (t / 100) % 2;   // APPROVED: quick flutter
  else st = (t % 3000) < 60;
  digitalWrite(PIN_LED_STATUS, st);
  digitalWrite(PIN_LED_XIAO, mode == REC ? ((t / 500) % 2) : HIGH);   // the XIAO's own LED mirrors REC (active low)
}

// ---------- power off: deep sleep, wake on APPROVE (D2 = GPIO3) ----------
void powerOff() {
  Serial.println("POWER OFF -> deep sleep; hold APPROVE 1.5 s to wake");
  if (oledOK) { oled.clearBuffer(); oled.setFont(u8g2_font_10x20_tf); oled.drawStr(34, 38, "bye"); oled.sendBuffer(); delay(500); oled.setPowerSave(1); }
  digitalWrite(PIN_LED_STATUS, LOW); digitalWrite(PIN_LED_REC, LOW); digitalWrite(PIN_LED_XIAO, HIGH);
  if (micOK) i2s.end();
  if (imuOK) { imuWrite(IMU_CTRL1, 0x00); imuWrite(IMU_CTRL2, 0x00); }   // accel + gyro power-down
  while (digitalRead(PIN_BTN_MARK) == LOW || digitalRead(PIN_BTN_APPROVE) == LOW) delay(10);   // wait for release, or we'd wake at once
  delay(100);
  rtc_gpio_pullup_en((gpio_num_t)PIN_BTN_APPROVE); rtc_gpio_pulldown_dis((gpio_num_t)PIN_BTN_APPROVE);
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_BTN_APPROVE, 0);          // wake when APPROVE goes LOW
  esp_deep_sleep_start();
}

// On a wake from deep sleep, APPROVE must stay held 1.5 s or we go straight back to sleep (a pocket can't power it on).
void confirmWake() {
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_EXT0) return;
  pinMode(PIN_BTN_APPROVE, INPUT_PULLUP);
  uint32_t t0 = millis();
  while (digitalRead(PIN_BTN_APPROVE) == LOW) {
    if (millis() - t0 >= 1500) { digitalWrite(PIN_LED_STATUS, HIGH); delay(200); digitalWrite(PIN_LED_STATUS, LOW); return; }
    delay(10);
  }
  rtc_gpio_pullup_en((gpio_num_t)PIN_BTN_APPROVE); rtc_gpio_pulldown_dis((gpio_num_t)PIN_BTN_APPROVE);
  esp_sleep_enable_ext0_wakeup((gpio_num_t)PIN_BTN_APPROVE, 0);
  esp_deep_sleep_start();
}

// ---------- setup ----------
void i2cScan() {
  i2cFound = "";
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      char h[6]; snprintf(h, sizeof h, "%02X ", a);
      i2cFound += h;
      const char* what = a == 0x3C ? "OLED" : (a == 0x6A || a == 0x6B) ? "IMU" : (a == 0x77 || a == 0x76) ? "BME680" : "?";
      Serial.printf("  0x%02X  %s\n", a, what);
    }
  }
  if (i2cFound.isEmpty()) Serial.println("  nothing on the bus: check 3V3/GND and SDA=D4, SCL=D5");
}

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println("\n==== brooch-bench: XIAO ESP32S3 Sense + OLED + IMU + BME680 + 3 buttons + 2 LEDs ====");
  pinMode(PIN_LED_XIAO, OUTPUT); digitalWrite(PIN_LED_XIAO, HIGH);
  pinMode(PIN_LED_STATUS, OUTPUT); pinMode(PIN_LED_REC, OUTPUT);
  confirmWake();                                   // back to sleep unless APPROVE is held 1.5 s
  for (int i = 0; i < 3; i++) {                     // LED self-test: both blink three times
    digitalWrite(PIN_LED_STATUS, HIGH); digitalWrite(PIN_LED_REC, HIGH); delay(120);
    digitalWrite(PIN_LED_STATUS, LOW);  digitalWrite(PIN_LED_REC, LOW);  delay(120);
  }
  bMark.begin(PIN_BTN_MARK, 700); bTalk.begin(PIN_BTN_TALK, 300); bApprove.begin(PIN_BTN_APPROVE, 2000);
  pinMode(PIN_SPARE, INPUT_PULLUP);

  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Serial.println("[1] I2C scan (expect 0x3C OLED, 0x6A/0x6B IMU, 0x77 BME680)");
  i2cScan();

  Serial.println("[2] OLED");
  oled.setI2CAddress(0x3C << 1);
  oledOK = oled.begin();
  Serial.println(oledOK ? "  ok" : "  u8g2 begin failed");
  if (oledOK) { oled.clearBuffer(); oled.setFont(u8g2_font_10x20_tf); oled.drawStr(4, 38, "EXO BROOCH"); oled.sendBuffer(); }

  Serial.println("[3] IMU LSM6DSV16X");
  imuOK = imuBegin();
  Serial.println(imuOK ? "  ok" : "  not found (0x6A/0x6B, or WHO_AM_I wrong; if absent, tie the CS pad to 3V3)");

  Serial.println("[4] BME680");
  bmeOK = bme.begin(0x77) || bme.begin(0x76);
  if (bmeOK) {
    bme.setTemperatureOversampling(BME680_OS_8X);
    bme.setHumidityOversampling(BME680_OS_2X);
    bme.setPressureOversampling(BME680_OS_4X);
    bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
    bme.setGasHeater(320, 150);
    Serial.println("  ok");
  } else Serial.println("  not found (0x77 as shipped; 0x76 if J2 is bridged)");

  Serial.println("[5] PDM mic (CLK 42, DATA 41)");
  i2s.setPinsPdmRx(42, 41);
  micOK = i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  Serial.println(micOK ? "  ok" : "  I2S PDM driver failed to start");

  Serial.printf("RESULT oled=%d imu=%d bme=%d mic=%d i2c=[%s]\n", oledOK, imuOK, bmeOK, micOK, i2cFound.c_str());
  Serial.println("MARK short=mark hold=rec | TALK tap=next screen hold=talk | APPROVE short=approve hold2s=freeze | MARK+TALK 1s=private | MARK+APPROVE 3s=power off");
}

// ---------- loop ----------
void loop() {
  bMark.poll(); bTalk.poll(); bApprove.poll();

  // Chord: MARK + TALK held together for 1 s toggles PRIVATE; while both are down the single-button actions are suppressed
  if (bMark.down && bTalk.down) {
    if (!chord) { chord = true; chordAt = millis(); chordFired = false; talking = false; }
    if (!chordFired && millis() - chordAt >= 1000 && !frozen) {
      chordFired = true; mode = mode == PRIVATE ? LIFELOG : PRIVATE; Serial.printf("MARK+TALK -> %s\n", modeName());
    }
  } else if (chord && !bMark.down && !bTalk.down) { chord = false; bMark.shortPress = bTalk.shortPress = false; }   // the last release isn't a tap

  // Power-off chord: MARK + APPROVE held 3 s. Suppresses APPROVE's own actions while both are down.
  if (bMark.down && bApprove.down) {
    if (!offChord) { offChord = true; offChordAt = millis(); talking = false; }
    if (millis() - offChordAt >= 3000) powerOff();                 // never returns
  } else if (offChord && !bMark.down && !bApprove.down) { offChord = false; bMark.shortPress = bApprove.shortPress = false; }

  // FREEZE wins over everything: only APPROVE held 2 s clears it
  if (bApprove.longPress && !offChord) { frozen = !frozen; if (frozen) { mode = LIFELOG; talking = false; } Serial.printf("APPROVE hold -> %s\n", frozen ? "FREEZE" : "unfrozen"); }
  if (!frozen && !chord && !offChord && mode != PRIVATE) {
    if (bMark.shortPress) { marks++; char s[24]; snprintf(s, sizeof s, "moment #%lu", (unsigned long)marks); flash("MARK", s); Serial.printf("MARK short -> #%lu\n", (unsigned long)marks); }
    if (bMark.longPress)  { mode = mode == REC ? LIFELOG : REC; Serial.printf("MARK hold -> %s\n", modeName()); }
    if (bTalk.longPress)  { talking = true; Serial.println("TALK hold -> listening"); }        // hold past 300 ms = push-to-talk
    if (bTalk.shortPress) { screen = (screen + 1) % SCREENS; Serial.printf("TALK tap -> screen %d\n", screen); }
    if (talking && !bTalk.down) { talking = false; Serial.println("TALK released -> sent"); }
    if (bApprove.shortPress) { approvals++; char s[24]; snprintf(s, sizeof s, "#%lu sent to the Pi", (unsigned long)approvals); flash("APPROVED", s); Serial.printf("APPROVE short -> #%lu\n", (unsigned long)approvals); }
  } else talking = false;

  if (imuOK) imuPoll();
  micPoll();                                        // 50 ms of audio; this paces the loop

  static uint32_t lastBme = 0;
  if (bmeOK && millis() - lastBme > 3000 && mode != PRIVATE) {
    lastBme = millis();
    if (bme.performReading()) {
      temperature = bme.temperature; humidity = bme.humidity;
      pressure = bme.pressure / 100.0f; gasKohm = bme.gas_resistance / 1000.0f;
    }
  }

  static uint32_t lastDraw = 0;
  if (millis() - lastDraw > 100) { lastDraw = millis(); drawScreen(); }
  updateLeds();

  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 2000) {
    lastPrint = millis();
    Serial.printf("mode %-7s scr %d%s | T %.1fC RH %.0f%% P %.1fhPa gas %.0fk | pitch %4.0f roll %4.0f |a| %.2f steps %lu | mic rms %4d peak %5d%s\n",
                  modeName(), screen, talking ? " TALK" : "", temperature, humidity, pressure, gasKohm, pitchDeg, rollDeg, aMag,
                  (unsigned long)steps, micRms, micPeak, micRms > 300 ? " <-- sound" : "");
  }
}

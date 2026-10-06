# Pin map

Copied verbatim from [[exo-hardware-priorities]] §2.1–2.2.

### 2.1 Button map (same wires as before, new roles)

| Control | Part | GPIO (pin) | GND pin | Action |
|---|---|---|---|---|
| **Approve** | **NOWNodes keyswitch keychain** | GPIO 5 (29) | 30 | **One press = approve and send the oldest queued transaction** (manual mode). Does nothing in auto mode or when the queue is empty |
| **Talk** (master agent STT) | Tactile | GPIO 26 (37) | 39 | **Press = start listening at once, release = send to the master agent.** A press under 0.3 s cancels; two quick taps repeat the last reply |
| **Camera** | Tactile | GPIO 6 (31) | 34 | Tap = **one photo**; double-tap = camera on/off (photo every 30 s) |
| **Mic** | Tactile | GPIO 13 (33) | 34 | Tap = **memo start / stop** |
| Kiosk (optional) | Tactile | GPIO 16 (36) | 39 | Tap = dashboard ↔ desktop. Ctrl+Alt+K does the same from the keyboard |

Each button goes **between its GPIO and GND**, with the Pi's internal pull-ups. No resistors needed. Reserved: I2C GPIO 2/3, UART GPIO 14/15 (PN532), I2S GPIO 18/19/20 (mic), GPIO 12 (AI City door servo).

### 2.2 Light map

| Light | Colour | GPIO (pin) | Resistor | Meaning |
|---|---|---|---|---|
| **Approve key LED** | Green (in the keychain) | GPIO 17 (11) | 100 Ω | **Blinks while transactions wait for your press**; 3 fast flashes when one is sent; off when the queue is empty or in auto mode |
| Listening | Blue or white | GPIO 25 (22) | 100 Ω | On while Talk is held |
| Camera / mic | **Red** | GPIO 22 (15) | 330 Ω | **On while anything records**, for bystanders. Follows the clip's own report |
| CRE verdict | Green | GPIO 23 (16) | 330 Ω | 1 flash approve, 2 cut, 3 refuse |
| Waiting for cold key | Yellow | GPIO 24 (18) | 330 Ω | Blinks while an over-cap action waits for the Pi Zero |
| Spare | any | GPIO 27 (13) | — | — |

Wiring per LED: **GPIO → resistor → LED long leg (+); short leg (−) → GND** (pins 9, 14, 20, 25). Red, yellow and green LEDs drop ~2 V, so 330 Ω gives ~4 mA. Blue, white and most bright greens drop ~3 V, so they need the smaller 100 Ω to be visible from 3.3 V. Keep each pin under ~10 mA. Take the resistors from the assortment (#14).

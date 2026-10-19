# T529: ESP32 hardware spec discovery

Establish the real hardware before any firmware is written. Per §69.9, the
display controller and pin mapping are **not assumed**; everything known is
recorded here and everything unknown is listed as a blocker.

## Known (from the specification)

| Item | Value | Source |
|---|---|---|
| MCU module | ESP32-WROOM-32 | `server_arc.md` §69.9 |
| Display size | 2.4 inch | `server_arc.md` §69.9 |
| Form | Standalone embedded touchscreen UI | `server_arc.md` §69.9 |
| Role | Thin client over §64.9; never runs AI models, never renders the desktop UI | §69.9 / D044 |

## Unknown — blockers

No hardware datasheet or vendor listing is present in this repository, and no
physical board was inspected. Each item below **must be confirmed from the actual
hardware/vendor before firmware work**:

| Unknown | Why it matters | Impact if assumed wrongly |
|---|---|---|
| Display controller (e.g. ILI9341 / ST7789 / other) | Drives init sequence, colour order, offset and inversion | Garbled or blank display |
| Display resolution / driver IC variant | Some "2.4 inch" panels are 240×320 with an offset | Wrong windowing, shifted image |
| Touch controller (e.g. XPT2046 / FT6236 / other) | Drives the touch library and calibration | No or mis-mapped touch input |
| Touch wiring (shared SPI vs separate chip-select) | Determines bus sharing and CS pins | Bus contention |
| SPI pins (SCLK/MOSI/MISO/CS/DC/RST/BL) | Required `TFT_eSPI` `User_Setup.h` | Firmware will not drive the panel |
| Touch SPI pins + IRQ | Required touch library setup | Touch will not read |
| Backlight control (GPIO / PWM / fixed) | Determines dimming | No brightness control |
| Touch calibration constants | Required for accurate taps | Taps land off-target |
| Power supply and 3.3 V budget | WROOM-32 + panel current | Brownouts |
| Any on-board peripheral (SD, buzzer, buttons) | Affects available GPIO | Pin conflicts |

## Required `User_Setup.h` values (to be filled once known)

`TFT_eSPI` configuration cannot be completed until these are confirmed:

```
#define TFT_DRIVER      <unknown>
#define TFT_WIDTH       <unknown>
#define TFT_HEIGHT      <unknown>
#define TFT_MISO        <unknown>
#define TFT_MOSI        <unknown>
#define TFT_SCLK        <unknown>
#define TFT_CS          <unknown>
#define TFT_DC          <unknown>
#define TFT_RST         <unknown>
#define TFT_BL          <unknown>
#define TOUCH_CS        <unknown>
```

## Acceptance

- The document lists **known + unknown** (this file).
- **No pin mapping and no controller is assumed**; firmware tasks T531–T533
  cannot be verified until this table's unknowns are resolved.
- Hardware verification is **not possible in this repository/session** — no board
  is present. Per D045, this is recorded as a blocker, not as passing.

## Concrete next step (for whoever has the board)

1. Identify the panel and touch controller from the vendor listing / silkscreen.
2. Probe the SPI and touch lines to confirm the pin map.
3. Record the confirmed values here, then fill `TFT_eSPI/User_Setup.h`.
4. Proceed to T530 (library evaluation) with real constraints.

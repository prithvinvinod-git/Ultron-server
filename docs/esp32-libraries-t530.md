# T530: ESP32 UI library evaluation

Choose libraries that fit the **actual hardware** (T529) and the WROOM-32
budget. No library is adopted before this evaluation (§69.9).

## Constraint

T529 left the display controller, resolution, touch controller and pin map
**unknown**. That blocks a final `TFT_eSPI` `User_Setup.h`. The evaluation below
records the trade-offs and a *provisional* recommendation, explicitly gated on
T529's unknowns being resolved on the real board.

## Candidates

### Display: `TFT_eSPI`

| Aspect | Assessment |
|---|---|
| Compatibility | Best-in-class if the panel is ILI9341/ST7789-class; configured via `User_Setup.h` (needs T529 to fill). |
| Footprint | Moderate; measured in flash, acceptable for a UI build. |
| Partial redraw | Supported — `setAddrWindow`/`pushImage` for bounded regions; no full framebuffer needed. |
| DMA | Supported on ESP32; enables non-blocking pushes. |
| Risk | Requires exact panel/touch pin knowledge (the T529 blocker). |
| Footprint figure | **Not measured here** (no toolchain); must be measured on build. |

### Touch: `XPT2046_Touchscreen`

| Aspect | Assessment |
|---|---|
| Compatibility | Only if the touch controller is an XPT2046 (T529 unknown). |
| Footprint | Small. |
| Integration | Shares SPI with the display via a second CS; needs calibration constants. |
| Risk | Wrong if the controller is capacitive (e.g. FT6236) — then a different driver is needed. |

### `LVGL`

| Aspect | Assessment |
|---|---|
| Capability | Full widget toolkit, partial redraw via dirty rectangles, animations. |
| Footprint | Heavy for a WROOM-32 UI; RAM/flash pressure. |
| Complexity | Higher build/config cost; overkill for seven simple screens. |
| Verdict | **Rejected for now** — premature for a compact status UI on WROOM-32 budget. |

## Recommendation (provisional)

- **Display:** `TFT_eSPI` — conditional on T529 confirming an ILI9341/ST7789-class
  panel and its pins.
- **Touch:** `XPT2046_Touchscreen` if (and only if) T529 confirms an XPT2046;
  otherwise select the matching capacitive controller driver.
- **Rejected:** `LVGL` (footprint/complexity for the WROOM-32 and seven screens).
- **Rendering approach:** direct partial redraws with `TFT_eSPI` region pushes;
  no full framebuffer allocation.

The classic Arduino-ESP32 core (or PlatformIO) is assumed, **not yet chosen** —
that choice belongs with the build skeleton (T531) once the toolchain target is
set.

## Acceptance

- Decision recorded with the trade-offs and rejected options (this file).
- **Footprint figures are unverified** — no toolchain is installed here, so sizes
  must be measured on a real build (T534).
- The recommendation is **conditional** on T529; it is not an adoption until the
  hardware is identified.

## Blockers carried forward

1. T529 unknowns (controller, resolution, touch IC, pins).
2. Toolchain selection (Arduino-ESP32 vs PlatformIO) — deferred to T531/T534.
3. Footprint measurement — blocked on a working build.

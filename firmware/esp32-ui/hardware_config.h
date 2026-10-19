// Hardware configuration for the ESP32 touchscreen UI.
//
// Per §69.9 and T529, NO pin mapping and NO controller is assumed. Every value
// below is an unresolved placeholder. This file deliberately FAILS TO COMPILE
// until the real hardware is identified and these values are filled in from
// docs/esp32-hardware-t529.md. That is the point: firmware cannot silently
// target the wrong panel.
//
// Do not comment out the #error below to "make it build" without first filling
// in real, verified values.

#ifndef ULTRON_HARDWARE_CONFIG_H
#define ULTRON_HARDWARE_CONFIG_H

#if !defined(ULTRON_HARDWARE_CONFIRMED)
#error "Hardware not confirmed (T529). Fill in display/touch controller, resolution and pin map, then define ULTRON_HARDWARE_CONFIRMED."
#endif

// --- Display ---------------------------------------------------------------
// TODO(T529): set to the real driver, e.g. ILI9341_DRIVER or ST7789_DRIVER.
#define ULTRON_TFT_DRIVER       0
#define ULTRON_TFT_WIDTH        0  // TODO(T529)
#define ULTRON_TFT_HEIGHT       0  // TODO(T529)

// --- Shared/display SPI ----------------------------------------------------
#define ULTRON_PIN_TFT_MISO     0  // TODO(T529)
#define ULTRON_PIN_TFT_MOSI     0  // TODO(T529)
#define ULTRON_PIN_TFT_SCLK     0  // TODO(T529)
#define ULTRON_PIN_TFT_CS       0  // TODO(T529)
#define ULTRON_PIN_TFT_DC       0  // TODO(T529)
#define ULTRON_PIN_TFT_RST      0  // TODO(T529)
#define ULTRON_PIN_TFT_BL       0  // TODO(T529) (or -1 if fixed backlight)

// --- Touch -----------------------------------------------------------------
// TODO(T529): identify controller (XPT2046 assumed only as a placeholder).
#define ULTRON_TOUCH_CS         0  // TODO(T529)
#define ULTRON_TOUCH_IRQ        0  // TODO(T529) (or -1 if polling)

// Touch calibration constants (raw min/max) — TODO(T529), measured on hardware.
#define ULTRON_TOUCH_X_MIN      0
#define ULTRON_TOUCH_X_MAX      0
#define ULTRON_TOUCH_Y_MIN      0
#define ULTRON_TOUCH_Y_MAX      0

#endif  // ULTRON_HARDWARE_CONFIG_H

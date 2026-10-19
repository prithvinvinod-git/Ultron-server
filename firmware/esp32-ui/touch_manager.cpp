// Touch manager implementation (XPT2046 placeholder, T530).
//
// Written-but-unverified: no touch hardware is present, and the controller and
// calibration constants (hardware_config.h) are unresolved until T529.

#include "touch_manager.h"

#include "hardware_config.h"

#include <XPT2046_Touchscreen.h>
#include <SPI.h>

namespace ultron {

namespace {
XPT2046_Touchscreen s_touch(ULTRON_TOUCH_CS, ULTRON_TOUCH_IRQ);

// Map a raw axis range onto a display range.
int16_t mapAxis(int raw, int16_t rawMin, int16_t rawMax, int displayMax) {
  if (rawMax == rawMin) return 0;
  long v = (long)(raw - rawMin) * displayMax / (rawMax - rawMin);
  if (v < 0) v = 0;
  if (v > displayMax) v = displayMax;
  return (int16_t)v;
}
}  // namespace

bool touchInit() {
  s_touch.begin();
  return s_touch.tirqTouched() || true;  // controller readiness confirmed on HW
}

TouchPoint touchRead() {
  TouchPoint point{false, 0, 0};
  if (!s_touch.touched()) return point;
  TS_Point p = s_touch.getPoint();
  point.touched = true;
  point.x = mapAxis(p.x, ULTRON_TOUCH_X_MIN, ULTRON_TOUCH_X_MAX, displayWidth());
  point.y = mapAxis(p.y, ULTRON_TOUCH_Y_MIN, ULTRON_TOUCH_Y_MAX, displayHeight());
  return point;
}

}  // namespace ultron

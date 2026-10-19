// Touch manager: reads and calibrates the touch panel.
//
// The touch controller is unknown until T529; the placeholder driver is
// XPT2046 (T530). Swapping controllers means changing only this module.

#ifndef ULTRON_TOUCH_MANAGER_H
#define ULTRON_TOUCH_MANAGER_H

#include <stdint.h>

namespace ultron {

struct TouchPoint {
  bool touched;
  int16_t x;  // calibrated, in display coordinates
  int16_t y;
};

// Initialise the touch controller. Returns false if it did not respond.
bool touchInit();

// Read one touch sample (non-blocking). Returns a point with touched=false when
// the panel is not being pressed.
TouchPoint touchRead();

}  // namespace ultron

#endif  // ULTRON_TOUCH_MANAGER_H

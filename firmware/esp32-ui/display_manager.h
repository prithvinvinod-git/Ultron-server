// Display manager: initialisation and partial-region redraws.
//
// Thin wrapper over the chosen display library (T530: TFT_eSPI). It exists so
// screen code never touches raw display calls, and so partial redraws are the
// only drawing path — screens must not allocate a full framebuffer.

#ifndef ULTRON_DISPLAY_MANAGER_H
#define ULTRON_DISPLAY_MANAGER_H

#include <stdint.h>

namespace ultron {

// Initialise the panel, backlight and rotation. Returns false if the panel did
// not initialise (the caller should show the Connection/error screen).
bool displayInit();

int displayWidth();
int displayHeight();

// Drawing primitives. All operate on bounded regions; none allocate a
// full-screen buffer.
void drawText(int16_t x, int16_t y, const char* text, uint16_t colour);
void drawTextCentered(int16_t y, const char* text, uint16_t colour);
void fillRegion(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour);
void drawHLine(int16_t x, int16_t y, int16_t w, uint16_t colour);
void drawFrame(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour);

// A named colour palette so screens share one set of semantic colours.
namespace palette {
constexpr uint16_t kBackground = 0x0000;  // black
constexpr uint16_t kForeground = 0xFFFF;  // white
constexpr uint16_t kPrimary    = 0x07FF;  // cyan
constexpr uint16_t kMuted      = 0x8410;  // grey
constexpr uint16_t kAlert      = 0xF800;  // red
constexpr uint16_t kOk         = 0x07E0;  // green
}  // namespace palette

}  // namespace ultron

#endif  // ULTRON_DISPLAY_MANAGER_H

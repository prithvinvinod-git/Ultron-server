// Display manager implementation (TFT_eSPI, T530).
//
// Physical panel wiring comes from hardware_config.h, which is an #error gate
// until T529 is resolved. This translation unit therefore cannot be compiled
// against real hardware here; it is written-but-unverified.

#include "display_manager.h"

#include "hardware_config.h"

#include <TFT_eSPI.h>

namespace ultron {

namespace {
TFT_eSPI s_tft;
bool s_ready = false;
}  // namespace

bool displayInit() {
  s_tft.init();
  s_tft.setRotation(1);  // landscape: width x height per T529
  s_tft.fillScreen(palette::kBackground);
#if ULTRON_PIN_TFT_BL >= 0
  pinMode(ULTRON_PIN_TFT_BL, OUTPUT);
  digitalWrite(ULTRON_PIN_TFT_BL, HIGH);
#endif
  s_ready = true;
  return s_ready;
}

int displayWidth() { return s_tft.width(); }
int displayHeight() { return s_tft.height(); }

void drawText(int16_t x, int16_t y, const char* text, uint16_t colour) {
  s_tft.setTextColor(colour, palette::kBackground);
  s_tft.setCursor(x, y);
  s_tft.print(text);
}

void drawTextCentered(int16_t y, const char* text, uint16_t colour) {
  s_tft.setTextColor(colour, palette::kBackground);
  s_tft.setTextDatum(TC_DATUM);
  s_tft.drawString(text, s_tft.width() / 2, y);
  s_tft.setTextDatum(TL_DATUM);
}

void fillRegion(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour) {
  s_tft.fillRect(x, y, w, h, colour);
}

void drawHLine(int16_t x, int16_t y, int16_t w, uint16_t colour) {
  s_tft.drawFastHLine(x, y, w, colour);
}

void drawFrame(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t colour) {
  s_tft.drawRect(x, y, w, h, colour);
}

}  // namespace ultron

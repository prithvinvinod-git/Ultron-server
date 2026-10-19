// ULTRON ESP32 touchscreen UI — main sketch (T531).
//
// Thin companion UI over the ESP32 protocol subset (T525). It never runs AI
// models and never renders the desktop interface (§69.9, D044).
//
// Written-but-unverified: no toolchain and no hardware are present, and the
// hardware configuration (T529) is unresolved. See README.md.
//
// loop() is non-blocking: display, touch, network and client are all ticked
// time-sliced, and rendering only happens when state or screen changes.

#include "display_manager.h"
#include "touch_manager.h"
#include "screen_manager.h"
#include "network_manager.h"
#include "ultron_client.h"

using namespace ultron;

namespace {
AppState g_state{false, false, "idle", "-", "-", "", nullptr};
Screen g_lastScreen = Screen::Count;
unsigned long g_lastTouch = 0;
constexpr unsigned long kTouchDebounceMs = 250;

// Configure these for the deployment; kept out of hardware_config.h because they
// are provisioning values, not board wiring.
const char* kWifiSsid = "ULTRON";
const char* kWifiPassword = "";
const char* kServerHost = "192.168.1.10";  // TODO: provision at deploy time
constexpr unsigned short kServerPort = 8000;
const char* kDeviceId = "esp32-ui-01";
const char* kDeviceToken = nullptr;  // from /auth/login (T525 subset)
}  // namespace

void setup() {
  Serial.begin(115200);
  if (!displayInit()) {
    // No panel: nothing to render. Stay in a defined state.
    g_state.errorText = "display init failed";
    screenShow(Screen::Connection);
    return;
  }
  touchInit();
  networkInit(NetworkConfig{kWifiSsid, kWifiPassword, kServerHost, kServerPort});
  clientInit(ClientConfig{kServerHost, kServerPort, kDeviceId, kDeviceToken});
  screenInit();
  screenShow(Screen::Home);
  screenRender(g_state);
  g_lastScreen = screenCurrent();
}

void loop() {
  networkTick();
  clientTick();

  clientPollState(g_state);
  g_state.connected = networkConnected();

  // Touch: debounced, non-blocking.
  unsigned long now = millis();
  if (now - g_lastTouch >= kTouchDebounceMs) {
    TouchPoint p = touchRead();
    if (p.touched) {
      g_lastTouch = now;
      screenHandleTouch(p.x, p.y);
    }
  }

  // Render only on change (partial regions inside each screen).
  if (screenCurrent() != g_lastScreen) {
    screenRender(g_state);
    g_lastScreen = screenCurrent();
  }
}

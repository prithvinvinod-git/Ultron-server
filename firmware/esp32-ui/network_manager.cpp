// Network manager implementation (T533).
//
// Written-but-unverified: no network was run on hardware. Uses WiFi + a
// time-sliced reconnect with backoff so the UI never blocks.

#include "network_manager.h"

#include <WiFi.h>

namespace ultron {

namespace {
NetworkConfig s_config{};
unsigned long s_lastAttempt = 0;
unsigned long s_backoffMs = 1000;
constexpr unsigned long kBackoffMaxMs = 30000;

void beginConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(s_config.ssid, s_config.password);
  s_lastAttempt = millis();
}
}  // namespace

void networkInit(const NetworkConfig& config) {
  s_config = config;
  beginConnect();
}

void networkTick() {
  if (WiFi.status() == WL_CONNECTED) return;
  unsigned long now = millis();
  if (now - s_lastAttempt < s_backoffMs) return;
  // Exponential backoff, capped. Never blocks loop().
  s_backoffMs = (s_backoffMs * 2 > kBackoffMaxMs) ? kBackoffMaxMs : s_backoffMs * 2;
  beginConnect();
}

bool networkConnected() { return WiFi.status() == WL_CONNECTED; }

}  // namespace ultron

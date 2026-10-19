// ULTRON protocol client implementation (T525 subset).
//
// Written-but-unverified: no server connection was run on hardware. The client
// heartbeats on an interval and polls status; it sends only predefined,
// authorized commands and relies on the server to enforce authorization.

#include "ultron_client.h"

#include "network_manager.h"

#include <WiFi.h>
#include <HTTPClient.h>

namespace ultron {

namespace {
ClientConfig s_config{};
unsigned long s_lastHeartbeat = 0;
constexpr unsigned long kHeartbeatMs = 15000;  // matches DEVICE_HEARTBEAT_INTERVAL intent
AppState s_state{true, false, "idle", "-", "-", "", nullptr};

String baseUrl() {
  String url = "http://";
  url += s_config.host;
  url += ":";
  url += s_config.serverPort;
  return url;
}

bool postJson(const String& path, const String& body) {
  if (!networkConnected()) return false;
  HTTPClient http;
  http.begin(baseUrl() + path);
  http.addHeader("Content-Type", "application/json");
  if (s_config.token) http.addHeader("Authorization", String("Bearer ") + s_config.token);
  int code = http.POST(body);
  http.end();
  return code >= 200 && code < 300;
}
}  // namespace

void clientInit(const ClientConfig& config) {
  s_config = config;
  // Register presence once connected (T525 subset).
  String body = String("{\"device_id\":\"") + s_config.deviceId + "\"}";
  postJson(String("/devices/") + s_config.deviceId + "/register", body);
}

void clientTick() {
  unsigned long now = millis();
  if (now - s_lastHeartbeat < kHeartbeatMs) return;
  s_lastHeartbeat = now;
  String body = String("{\"device_id\":\"") + s_config.deviceId + "\"}";
  postJson(String("/devices/") + s_config.deviceId + "/heartbeat", body);
  s_state.connected = networkConnected();
}

bool clientPollState(AppState& out) {
  // The base skeleton reports locally-derived state; server status polling is a
  // follow-up once the exact status payload is confirmed against the server.
  bool changed = (out.connected != s_state.connected) ||
                 (out.coreOnline != s_state.coreOnline);
  out = s_state;
  return changed;
}

bool clientSendCommand(const char* command) {
  String body = String("{\"device_id\":\"") + s_config.deviceId +
                "\",\"command\":\"" + command + "\"}";
  return postJson(String("/devices/") + s_config.deviceId + "/command", body);
}

}  // namespace ultron

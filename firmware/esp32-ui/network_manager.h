// Network manager: WiFi + server connection state, non-blocking reconnect.
//
// The UI never blocks on the network (§69.9, T533): the manager is polled from
// loop() and exposes its state; screens render that state.

#ifndef ULTRON_NETWORK_MANAGER_H
#define ULTRON_NETWORK_MANAGER_H

namespace ultron {

struct NetworkConfig {
  const char* ssid;
  const char* password;
  const char* serverHost;
  unsigned short serverPort;
};

void networkInit(const NetworkConfig& config);

// Advance connection/reconnection state (call from loop(); must not block).
void networkTick();

bool networkConnected();

}  // namespace ultron

#endif  // ULTRON_NETWORK_MANAGER_H

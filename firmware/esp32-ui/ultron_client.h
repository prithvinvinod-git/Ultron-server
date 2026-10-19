// ULTRON protocol client: the ESP32 protocol subset (T525).
//
// The device is a strict subset client (§69.7/§69.9): auth, device
// registration/presence, basic status and command results. It never reaches the
// Windows Node Bridge or any privileged operation (§69.2, D043/D044).

#ifndef ULTRON_CLIENT_H
#define ULTRON_CLIENT_H

#include "screen_manager.h"  // AppState

namespace ultron {

struct ClientConfig {
  const char* host;
  unsigned short port;
  const char* deviceId;
  const char* token;  // bearer token from /auth/login (T525 subset)
};

void clientInit(const ClientConfig& config);

// Advance the client (heartbeats, presence) without blocking loop().
void clientTick();

// Pull the latest server-reported state into `out`. Returns true if it changed.
bool clientPollState(AppState& out);

// Send a predefined, authorized command. Returns false if not connected or the
// server rejected it. The server remains authoritative for authorization.
bool clientSendCommand(const char* command);

}  // namespace ultron

#endif  // ULTRON_CLIENT_H

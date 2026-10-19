// Screen manager: the current-screen state machine and touch navigation.
//
// Screens are the seven from §69.9 (T532). Navigation is by touch; each screen
// renders from the shared AppState and only redraws the region it owns.

#ifndef ULTRON_SCREEN_MANAGER_H
#define ULTRON_SCREEN_MANAGER_H

#include <stdint.h>

namespace ultron {

enum class Screen : uint8_t {
  Home = 0,
  AiStatus,
  AgentStatus,
  Tasks,
  Chat,
  Settings,
  Connection,
  Count
};

// Shared, always-available device/app state that screens render from.
struct AppState {
  bool connected;
  bool coreOnline;
  const char* aiStatus;       // e.g. "idle" | "thinking"
  const char* agentSummary;   // e.g. "2 active / 1 idle"
  const char* taskSummary;    // e.g. "3 open / 1 done"
  const char* lastMessage;    // compact chat line
  const char* errorText;      // non-null on the Connection screen
};

void screenInit();
void screenShow(Screen screen);
Screen screenCurrent();

// Render the current screen from `state`. Called on change and on state updates;
// implementations must redraw partial regions only.
void screenRender(const AppState& state);

// Forward a tap to the current screen; returns the (possibly new) screen.
Screen screenHandleTouch(int16_t x, int16_t y);

}  // namespace ultron

#endif  // ULTRON_SCREEN_MANAGER_H

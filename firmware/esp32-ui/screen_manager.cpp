// Screen manager implementation (T531/T532/T533).
//
// Implements the current-screen state machine and touch navigation. Renders
// only on change or on an explicit state update; navigation is by touch.

#include "screen_manager.h"

#include "screens/screens.h"

namespace ultron {

namespace {
Screen s_current = Screen::Home;

Screen renderFor(Screen screen, const AppState& state) {
  switch (screen) {
    case Screen::Home:         return screens::renderHome(state);
    case Screen::AiStatus:     return screens::renderAiStatus(state);
    case Screen::AgentStatus:  return screens::renderAgentStatus(state);
    case Screen::Tasks:        return screens::renderTasks(state);
    case Screen::Chat:         return screens::renderChat(state);
    case Screen::Settings:     return screens::renderSettings(state);
    case Screen::Connection:   return screens::renderConnection(state);
    default:                   return Screen::Count;
  }
}
}  // namespace

void screenInit() { s_current = Screen::Home; }

void screenShow(Screen screen) {
  if (screen == Screen::Count) return;
  s_current = screen;
}

Screen screenCurrent() { return s_current; }

void screenRender(const AppState& state) {
  Screen next = renderFor(s_current, state);
  if (next == Screen::Count) return;
  // A screen may redirect (e.g. to Connection on error). A full clear on
  // transition is the only full-screen op, and only when the screen changes.
  if (next != s_current) {
    s_current = next;
    renderFor(s_current, state);
  }
}

Screen screenHandleTouch(int16_t x, int16_t y) {
  // Only taps in the nav strip navigate; body taps are handled per-screen.
  // The nav strip geometry lives in screens.cpp; ask it to interpret the tap.
  Screen tapped = screens::navHitTest(x, y);
  if (tapped != Screen::Count) s_current = tapped;
  // Base skeleton has no body actions, so the screen stays put otherwise.
  return s_current;
}

}  // namespace ultron

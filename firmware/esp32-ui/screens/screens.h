// The seven screens (§69.9, T532). Each renders a compact subset from AppState
// and returns the screen to navigate to (itself unless a navigation region was
// tapped).

#ifndef ULTRON_SCREENS_H
#define ULTRON_SCREENS_H

#include "../screen_manager.h"

namespace ultron {
namespace screens {

Screen renderHome(const AppState& state);
Screen renderAiStatus(const AppState& state);
Screen renderAgentStatus(const AppState& state);
Screen renderTasks(const AppState& state);
Screen renderChat(const AppState& state);
Screen renderSettings(const AppState& state);
Screen renderConnection(const AppState& state);

// Shared chrome: a title bar and a bottom navigation strip. Returns the screen
// selected by a tap in the nav strip, or Screen::Count if none.
Screen renderChrome(Screen current);

// Interpret a touch as a navigation-strip tap. Returns the target screen, or
// Screen::Count if the tap is outside the nav strip.
Screen navHitTest(int16_t x, int16_t y);

}  // namespace screens
}  // namespace ultron

#endif  // ULTRON_SCREENS_H

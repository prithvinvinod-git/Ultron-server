// The seven screens (§69.9, T532).
//
// Written-but-unverified: no display and no toolchain are present, so nothing
// here has been compiled or rendered. Each screen draws from AppState and only
// in bounded regions (no full framebuffer).

#include "screens.h"

#include "../display_manager.h"

namespace ultron {
namespace screens {

namespace {
constexpr int16_t kTitleY = 8;
constexpr int16_t kBodyY = 40;
constexpr int16_t kNavY = 200;  // TODO(T529): derive from displayHeight()

void title(const char* text) {
  fillRegion(0, 0, displayWidth(), 30, palette::kBackground);
  drawTextCentered(kTitleY, text, palette::kPrimary);
  drawHLine(0, 30, displayWidth(), palette::kMuted);
}

void navStrip() {
  fillRegion(0, kNavY, displayWidth(), displayHeight() - kNavY, palette::kBackground);
  drawHLine(0, kNavY, displayWidth(), palette::kMuted);
  // Seven evenly spaced nav cells; labels are abbreviated for width.
  static const char* kLabels[6] = {"HOME", "AI", "AGENT", "TASK", "CHAT", "SET"};
  for (int i = 0; i < 6; ++i) {
    int16_t cell = displayWidth() / 6;
    int16_t cx = i * cell + cell / 2;
    // drawText uses left origin; centre approximately.
    drawText(cx - 18, kNavY + 10, kLabels[i], palette::kMuted);
  }
}

}  // namespace

Screen navHitTest(int16_t x, int16_t y) {
  if (y < kNavY) return Screen::Count;
  if (x < 0) x = 0;
  int16_t cell = displayWidth() / 6;
  if (cell <= 0) return Screen::Count;
  switch (x / cell) {
    case 0: return Screen::Home;
    case 1: return Screen::AiStatus;
    case 2: return Screen::AgentStatus;
    case 3: return Screen::Tasks;
    case 4: return Screen::Chat;
    case 5: return Screen::Settings;
    default: return Screen::Count;
  }
}

Screen renderChrome(Screen current) {
  (void)current;
  navStrip();
  return Screen::Count;
}

Screen renderHome(const AppState& state) {
  title("ULTRON");
  drawText(10, kBodyY, state.coreOnline ? "CORE ONLINE" : "CORE STANDBY",
           state.coreOnline ? palette::kOk : palette::kAlert);
  drawText(10, kBodyY + 20, state.connected ? "NET CONNECTED" : "NET OFFLINE",
           state.connected ? palette::kOk : palette::kAlert);
  drawText(10, kBodyY + 40, state.agentSummary, palette::kForeground);
  drawText(10, kBodyY + 60, state.taskSummary, palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderAiStatus(const AppState& state) {
  title("AI STATUS");
  drawText(10, kBodyY, state.aiStatus ? state.aiStatus : "unknown", palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderAgentStatus(const AppState& state) {
  title("AGENTS");
  drawText(10, kBodyY, state.agentSummary ? state.agentSummary : "none", palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderTasks(const AppState& state) {
  title("TASKS");
  drawText(10, kBodyY, state.taskSummary ? state.taskSummary : "none", palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderChat(const AppState& state) {
  title("CHAT");
  drawText(10, kBodyY, state.lastMessage ? state.lastMessage : "(no messages)", palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderSettings(const AppState& state) {
  (void)state;
  title("SETTINGS");
  drawText(10, kBodyY, "Brightness / Backlight", palette::kForeground);
  drawText(10, kBodyY + 20, "Reconnect", palette::kForeground);
  navStrip();
  return Screen::Count;
}

Screen renderConnection(const AppState& state) {
  title("CONNECTION");
  drawText(10, kBodyY, state.connected ? "Connected" : "Disconnected",
           state.connected ? palette::kOk : palette::kAlert);
  if (state.errorText) {
    drawText(10, kBodyY + 20, state.errorText, palette::kAlert);
  }
  navStrip();
  return Screen::Count;
}

}  // namespace screens
}  // namespace ultron

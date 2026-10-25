/**
 * IPC channel constants shared between main and preload.
 * This module must NOT import from 'electron' — it is imported by both
 * the main process and the preload script, and pulling electron APIs
 * into the wrong context causes a crash.
 */

export const IPC_CHANNELS = {
  APP_VERSION: "app:version",
  BACKEND_INFO: "backend:info",
  LIFECYCLE_STATE: "lifecycle:state",
  LIFECYCLE_GET: "lifecycle:get",
  LIFECYCLE_REPORT: "lifecycle:report",
  WINDOW_MINIMIZE: "window:minimize",
  WINDOW_MAXIMIZE: "window:maximize",
  WINDOW_CLOSE: "window:close",
  WINDOW_GET_STATE: "window:get-state",
  WINDOW_SET_BOUNDS: "window:set-bounds",
  WINDOW_TOGGLE_FULLSCREEN: "window:toggle-fullscreen",
  WINDOW_SET_ALWAYS_ON_TOP: "window:set-always-on-top",
  // Floating orb (spec §1/§6). The orb renderer talks to the main process
  // through this narrow list; it never gets Node or the backend directly.
  ORB_GET_CONFIG: "orb:get-config",
  ORB_SET_VISIBLE: "orb:set-visible",
  ORB_SET_POSITION: "orb:set-position",
  ORB_MOVE: "orb:move",
  // Drag is driven by the main process (`screen.getCursorScreenPoint`), so the
  // window can never drift away from the orb the user is holding.
  ORB_DRAG_START: "orb:drag-start",
  ORB_DRAG_MOVE: "orb:drag-move",
  ORB_DRAG_END: "orb:drag-end",
  // Interactive geometry of the widget (orb disc + caption, in window CSS px).
  // Main polls the OS cursor against it to decide click-through; the renderer
  // cannot do that itself because a click-through window receives no moves.
  ORB_SET_HIT_REGION: "orb:set-hit-region",
  // main → orb renderer: the cursor is over/off the orb (hover state while the
  // window is not receiving pointer events).
  ORB_POINTER_INSIDE: "orb:pointer-inside",
  ORB_OPEN_MAIN: "orb:open-main",
  ORB_START_VOICE: "orb:start-voice",
  ORB_STOP_VOICE: "orb:stop-voice",
  ORB_CANCEL: "orb:cancel",
  ORB_QUICK_ACTION: "orb:quick-action",
  ORB_MENU: "orb:menu",
  ORB_HIDE: "orb:hide",
  ORB_QUIT: "orb:quit",
  // main → orb renderer
  ORB_CONFIG_CHANGED: "orb:config-changed",
  ORB_RECONNECT: "orb:reconnect",
  // console → main: show/hide and query the floating orb (the console is the
  // only way back once the orb hides itself)
  ORB_SHOW: "orb:show",
  ORB_GET_STATE: "orb:get-state",
  // main → console renderer: floating-orb visibility changed
  ORB_VISIBILITY: "orb:visibility",
  // main → console renderer: start/stop the existing live voice session
  VOICE_REQUEST: "voice:request",
  // Windows tool bridge (T262, §59.15)
  TOOL_LIST_WINDOWS: "tool:list_windows",
  TOOL_SCREENSHOT: "tool:screenshot",
  TOOL_CLICK: "tool:click",
  TOOL_TYPE: "tool:type",
  TOOL_KEYS: "tool:keys",
  TOOL_OPEN_APP: "tool:open_app",
  TOOL_CLOSE_APP: "tool:close_app",
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

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
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

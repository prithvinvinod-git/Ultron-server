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
} as const;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

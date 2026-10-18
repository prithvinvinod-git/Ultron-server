/**
 * ULTRON Electron preload bridge (T517).
 *
 * Exposes a minimal, typed API to the renderer via contextBridge. The
 * renderer has NO direct Node.js or OS access — everything goes through
 * these declared channels, and every channel is validated in main.
 *
 * Channel allow-list (add here, nowhere else):
 *   app:version       → get the Electron app version
 *   window:minimize   → minimize the window
 *   window:maximize   → maximize the window
 *   window:close      → close the window
 *
 * Any channel not listed here is rejected by ipcMain.handle.
 */
import { contextBridge, ipcRenderer } from "electron";

import { IPC_CHANNELS } from "./ipc-channels";

try {
  console.log("[preload] starting, contextBridge:", typeof contextBridge !== "undefined", "ipcRenderer:", typeof ipcRenderer !== "undefined");

  const api = {
    getAppVersion: (): Promise<string> =>
      ipcRenderer.invoke(IPC_CHANNELS.APP_VERSION) as Promise<string>,

    minimizeWindow: (): Promise<void> =>
      ipcRenderer.invoke(IPC_CHANNELS.WINDOW_MINIMIZE) as Promise<void>,

    maximizeWindow: (): Promise<void> =>
      ipcRenderer.invoke(IPC_CHANNELS.WINDOW_MAXIMIZE) as Promise<void>,

    closeWindow: (): Promise<void> =>
      ipcRenderer.invoke(IPC_CHANNELS.WINDOW_CLOSE) as Promise<void>,
  };

  contextBridge.exposeInMainWorld("ultron", api);
  console.log("[preload] ultron bridge exposed");
} catch (e) {
  console.error("[preload] FATAL:", e);
}

declare global {
  interface Window {
    ultron: {
      getAppVersion: () => Promise<string>;
      minimizeWindow: () => Promise<void>;
      maximizeWindow: () => Promise<void>;
      closeWindow: () => Promise<void>;
    };
  }
}

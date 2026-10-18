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

  getBackendInfo: (): Promise<{ backendUrl: string }> =>
    ipcRenderer.invoke(IPC_CHANNELS.BACKEND_INFO) as Promise<{ backendUrl: string }>,

  getLifecycleInfo: () =>
    ipcRenderer.invoke(IPC_CHANNELS.LIFECYCLE_GET) as Promise<{
      state: string;
      agentCount: number;
      taskCount: number;
      consecutiveFailures: number;
    }>,

  reportActivity: (agentCount: number, taskCount: number) =>
    ipcRenderer.invoke(IPC_CHANNELS.LIFECYCLE_REPORT, { agentCount, taskCount }) as Promise<void>,

  getWindowState: () =>
    ipcRenderer.invoke(IPC_CHANNELS.WINDOW_GET_STATE) as Promise<{
      isMaximized: boolean;
      isMinimized: boolean;
      isFullScreen: boolean;
      bounds: { x: number; y: number; width: number; height: number };
      isAlwaysOnTop: boolean;
    }>,

  setWindowBounds: (bounds: { x?: number; y?: number; width?: number; height?: number }) =>
    ipcRenderer.invoke(IPC_CHANNELS.WINDOW_SET_BOUNDS, bounds) as Promise<void>,

  toggleFullscreen: () =>
    ipcRenderer.invoke(IPC_CHANNELS.WINDOW_TOGGLE_FULLSCREEN) as Promise<void>,

  setAlwaysOnTop: (flag: boolean) =>
    ipcRenderer.invoke(IPC_CHANNELS.WINDOW_SET_ALWAYS_ON_TOP, flag) as Promise<void>,

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
      getBackendInfo: () => Promise<{ backendUrl: string }>;
      getLifecycleInfo: () => Promise<{
        state: string;
        agentCount: number;
        taskCount: number;
        consecutiveFailures: number;
      }>;
      reportActivity: (agentCount: number, taskCount: number) => Promise<void>;
      getWindowState: () => Promise<{
        isMaximized: boolean;
        isMinimized: boolean;
        isFullScreen: boolean;
        bounds: { x: number; y: number; width: number; height: number };
        isAlwaysOnTop: boolean;
      }>;
      setWindowBounds: (bounds: { x?: number; y?: number; width?: number; height?: number }) => Promise<void>;
      toggleFullscreen: () => Promise<void>;
      setAlwaysOnTop: (flag: boolean) => Promise<void>;
      minimizeWindow: () => Promise<void>;
      maximizeWindow: () => Promise<void>;
      closeWindow: () => Promise<void>;
    };
  }
}

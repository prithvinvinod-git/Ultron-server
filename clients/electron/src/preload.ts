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

import { IPC_CHANNELS } from "./ipc-channels.js";

try {
  console.log("[preload] starting, contextBridge:", typeof contextBridge !== "undefined", "ipcRenderer:", typeof ipcRenderer !== "undefined");

  interface VoiceRequest {
    action?: string;
    source?: string;
  }

  // Orb → console voice gestures. The orb asks the main process, which forwards
  // `voice:request` here; the console subscribes through the bridge below and
  // uses its existing live-session code. No second voice pipeline is introduced.
  const voiceListeners = new Set<(request: VoiceRequest) => void>();
  ipcRenderer.on(IPC_CHANNELS.VOICE_REQUEST, (_event, payload: VoiceRequest | null) => {
    for (const listener of voiceListeners) {
      try {
        listener(payload ?? {});
      } catch {
        // A failing listener must not break delivery to the others.
      }
    }
  });

  // Floating-orb visibility changes pushed from main (menu hide, SSE hide, …).
  // The console's "show orb" control stays in sync without polling.
  const orbVisibilityListeners = new Set<(state: { visible: boolean }) => void>();
  ipcRenderer.on(IPC_CHANNELS.ORB_VISIBILITY, (_event, payload: { visible?: unknown } | null) => {
    const state = { visible: payload?.visible === true };
    for (const listener of orbVisibilityListeners) {
      try {
        listener(state);
      } catch {
        // Isolated on purpose, same as voice requests.
      }
    }
  });

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

  /**
   * Subscribe to orb voice gestures forwarded by the main process. Returns an
   * unsubscribe function; the console calls it on unmount.
   */
  onVoiceRequest: (listener: (request: VoiceRequest) => void): (() => void) => {
    voiceListeners.add(listener);
    return () => {
      voiceListeners.delete(listener);
    };
  },

  // --- floating orb controls ---
  /** Show or hide the standalone orb window; resolves with its real state. */
  setOrbVisible: (visible: boolean): Promise<{ visible: boolean }> =>
    ipcRenderer.invoke(IPC_CHANNELS.ORB_SHOW, visible) as Promise<{ visible: boolean }>,

  getOrbState: (): Promise<{ visible: boolean }> =>
    ipcRenderer.invoke(IPC_CHANNELS.ORB_GET_STATE) as Promise<{ visible: boolean }>,

  onOrbVisibility: (listener: (state: { visible: boolean }) => void): (() => void) => {
    orbVisibilityListeners.add(listener);
    return () => {
      orbVisibilityListeners.delete(listener);
    };
  },

  // --- Windows tool bridge (T262, §59.15) ---
  listWindows: (): Promise<{ windows: any[] }> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_LIST_WINDOWS) as Promise<{ windows: any[] }>,
  screenshot: (args?: { window_id?: string; name?: string }): Promise<{ path: string }> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_SCREENSHOT, args) as Promise<{ path: string }>,
  click: (args: { x: number; y: number; button?: string; clicks?: number }): Promise<void> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_CLICK, args) as Promise<void>,
  type: (args: { text: string }): Promise<void> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_TYPE, args) as Promise<void>,
  keys: (args: { keys: string | string[] }): Promise<void> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_KEYS, args) as Promise<void>,
  openApplication: (args: { application: string; arguments?: string[] }): Promise<any> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_OPEN_APP, args) as Promise<any>,
  closeApplication: (args: { application?: string; window_id?: string }): Promise<any> =>
    ipcRenderer.invoke(IPC_CHANNELS.TOOL_CLOSE_APP, args) as Promise<any>,
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
      onVoiceRequest: (
        listener: (request: { action?: string; source?: string }) => void,
      ) => () => void;
      setOrbVisible: (visible: boolean) => Promise<{ visible: boolean }>;
      getOrbState: () => Promise<{ visible: boolean }>;
      onOrbVisibility: (listener: (state: { visible: boolean }) => void) => () => void;
      // Windows tool bridge
      listWindows: () => Promise<{ windows: any[] }>;
      screenshot: (args?: { window_id?: string; name?: string }) => Promise<{ path: string }>;
      click: (args: { x: number; y: number; button?: string; clicks?: number }) => Promise<void>;
      type: (args: { text: string }) => Promise<void>;
      keys: (args: { keys: string | string[] }) => Promise<void>;
      openApplication: (args: { application: string; arguments?: string[] }) => Promise<any>;
      closeApplication: (args: { application?: string; window_id?: string }) => Promise<any>;
    };
  }
}

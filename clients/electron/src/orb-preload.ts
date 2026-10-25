/**
 * Preload bridge for the floating orb window.
 *
 * Deliberately separate from `preload.ts`: the orb must not inherit the console
 * window's API surface (tools, filesystem-adjacent actions, window management).
 * It gets exactly the channels its gestures need, and no backend credentials.
 */
import { contextBridge, ipcRenderer } from "electron";

import { IPC_CHANNELS } from "./ipc-channels.js";

export interface OrbPosition {
  x: number;
  y: number;
}

export interface OrbConfig {
  position: OrbPosition;
  bounds: { width: number; height: number };
  visible: boolean;
  intensity: "low" | "normal" | "high";
  quickAction: "voice" | "open";
  origin: string;
  backendUrl: string;
  version: string;
}

/**
 * The only part of the frame that should accept input, in window CSS px. Main
 * polls the OS cursor against this to decide click-through, because a
 * click-through window gets no pointer events and cannot re-arm itself.
 */
export interface OrbHitRegion {
  x: number;
  y: number;
  radius: number;
  caption: { left: number; top: number; right: number; bottom: number } | null;
}

const api = {
  getConfig: (): Promise<OrbConfig | null> =>
    ipcRenderer.invoke(IPC_CHANNELS.ORB_GET_CONFIG) as Promise<OrbConfig | null>,
  setVisible: (visible: boolean): Promise<void> =>
    ipcRenderer.invoke(IPC_CHANNELS.ORB_SET_VISIBLE, visible) as Promise<void>,
  setPosition: (position: OrbPosition): Promise<void> =>
    ipcRenderer.invoke(IPC_CHANNELS.ORB_SET_POSITION, position) as Promise<void>,
  move: (position: OrbPosition): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_MOVE, position);
  },
  /**
   * Drag protocol: the renderer only says "still dragging"; the main process
   * reads the OS cursor and moves the window, so the orb and its frame can never
   * drift apart. `offset` is the grab point inside the window (DIP) and is only
   * a hint — main validates it against the live cursor.
   */
  dragStart: (offset: OrbPosition): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_DRAG_START, offset);
  },
  dragMove: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_DRAG_MOVE);
  },
  dragEnd: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_DRAG_END);
  },
  setHitRegion: (region: OrbHitRegion): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_SET_HIT_REGION, region);
  },
  openMain: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_OPEN_MAIN);
  },
  startVoice: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_START_VOICE);
  },
  stopVoice: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_STOP_VOICE);
  },
  cancel: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_CANCEL);
  },
  quickAction: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_QUICK_ACTION);
  },
  showMenu: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_MENU);
  },
  hide: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_HIDE);
  },
  quit: (): void => {
    ipcRenderer.send(IPC_CHANNELS.ORB_QUIT);
  },
  onConfigChanged: (listener: (config: Partial<OrbConfig>) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, config: Partial<OrbConfig>): void =>
      listener(config);
    ipcRenderer.on(IPC_CHANNELS.ORB_CONFIG_CHANGED, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.ORB_CONFIG_CHANGED, handler);
  },
  onReconnect: (listener: () => void): (() => void) => {
    const handler = (): void => listener();
    ipcRenderer.on(IPC_CHANNELS.ORB_RECONNECT, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.ORB_RECONNECT, handler);
  },
  onPointerInside: (listener: (inside: boolean) => void): (() => void) => {
    const handler = (_event: Electron.IpcRendererEvent, payload: { inside: boolean }): void =>
      listener(payload.inside);
    ipcRenderer.on(IPC_CHANNELS.ORB_POINTER_INSIDE, handler);
    return () => ipcRenderer.removeListener(IPC_CHANNELS.ORB_POINTER_INSIDE, handler);
  },
};

contextBridge.exposeInMainWorld("ultronOrb", api);

export type OrbBridge = typeof api;

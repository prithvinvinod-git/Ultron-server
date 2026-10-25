import { WebSocket } from "ws";
import { v4 as uuidv4 } from "uuid";
import { exec } from "child_process";
import { hostname } from "node:os";
import { join } from "node:path";

// Types for the protocol (duplicate from protocol.ts for now, but we could import from shared)
enum MessageType {
  REGISTER = "register",
  REGISTERED = "registered",
  HEARTBEAT = "heartbeat",
  HEARTBEAT_ACK = "heartbeat_ack",
  COMMAND = "command",
  RESULT = "result",
  GOODBYE = "goodbye",
}

interface NodeRegistration {
  type: MessageType;
  node_id: string;
  node_type: "windows" | "server" | "esp32" | "future";
  name: string;
  capabilities: string[];
  client_of?: string | null;
  version: string;
  firmware_version?: string | null;
  credential?: string | null;
}

interface ServerHello {
  type: MessageType;
  accepted: boolean;
  node_id: string;
  reason?: string | null;
  heartbeat_interval: number;
  offline_after: number;
  server_time: string; // ISO string
}

interface NodeHeartbeat {
  type: MessageType;
  node_id: string;
  status?: "online" | "offline" | "degraded";
  capabilities?: string[];
}

interface NodeHeartbeatAck {
  type: MessageType;
  node_id: string;
  server_time: string;
}

interface NodeCommand {
  type: MessageType;
  command_id: string;
  tool: string;
  arguments: Record<string, any>;
  principal?: string | null;
  created_at?: string;
  deadline?: string | null;
  correlation_id?: string | null;
  task_id?: string | null;
}

interface NodeResult {
  type: MessageType;
  command_id: string;
  node_id: string;
  status: "ok" | "error";
  result?: any;
  error_code?: string | null;
  error?: string | null;
  duration_ms?: number | null;
}

export class WindowsNode {
  private ws: WebSocket | null = null;
  private nodeId: string;
  private hostname: string;
  private capabilities: string[];
  private credential: string | null;
  private heartbeatInterval: NodeJS.Timeout | null = null;
  private readonly serverUrl: string;
  private readonly reconnectDelay = 5000; // ms

  constructor(serverUrl: string) {
    this.serverUrl = serverUrl;
    this.hostname = hostname() || "unknown";
    this.nodeId = `windows-${this.hostname.toLowerCase().replace(/\s+/g, "-")}`;
    // Generate a random credential for now; in production this should be securely stored
    this.credential = uuidv4();
    // Define the capabilities we intend to support
    this.capabilities = [
      "windows_ui",
      "screenshots",
      "windows_apps",
      // TODO: add more as implemented
    ];
  }

  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(`${this.serverUrl.replace(/^http/, "ws")}/nodes/ws`);
      this.ws.on("open", () => {
        console.log("[WindowsNode] WebSocket connected");
        this.register();
        resolve();
      });
      this.ws.on("error", (err) => {
        console.error("[WindowsNode] WebSocket error:", err);
        reject(err);
      });
      this.ws.on("close", (code, reason) => {
        console.log(`[WindowsNode] WebSocket closed (${code}): ${reason}`);
        this.cleanupHeartbeat();
        // Attempt reconnection after delay
        setTimeout(() => this.connect(), this.reconnectDelay);
      });
      this.ws.on("message", async (data: Buffer) => {
        try {
          const message = JSON.parse(data.toString());
          await this.handleMessage(message);
        } catch (err) {
          console.error("[WindowsNode] Failed to parse message:", err);
        }
      });
    });
  }

  stop(): void {
    this.cleanupHeartbeat();
    this.ws?.close();
  }

  private register(): void {
    if (!this.ws) return;
    const registerMsg: NodeRegistration = {
      type: MessageType.REGISTER,
      node_id: this.nodeId,
      node_type: "windows",
      name: `Ultron Windows Node (${this.hostname})`,
      capabilities: this.capabilities,
      client_of: undefined,
      version: "0.1.0",
      credential: this.credential,
    };
    this.ws.send(JSON.stringify(registerMsg));
    console.log("[WindowsNode] Sent registration:", registerMsg);
  }

  private async handleMessage(message: any): Promise<void> {
    switch (message.type) {
      case MessageType.REGISTERED:
        await this.handleRegistered(message);
        break;
      case MessageType.HEARTBEAT_ACK:
        // Nothing to do
        break;
      case MessageType.COMMAND:
        await this.handleCommand(message);
        break;
      default:
        console.warn("[WindowsNode] Unhandled message type:", message.type);
    }
  }

  private async handleRegistered(msg: ServerHello): Promise<void> {
    if (!msg.accepted) {
      console.error("[WindowsNode] Registration rejected:", msg.reason);
      this.ws?.close();
      return;
    }
    console.log("[WindowsNode] Registration accepted:", msg.node_id);
    this.setupHeartbeat(msg.heartbeat_interval);
  }

  private setupHeartbeat(interval: number): void {
    this.cleanupHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      this.sendHeartbeat();
    }, interval * 1000);
  }

  private cleanupHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  private sendHeartbeat(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const heartbeat: NodeHeartbeat = {
      type: MessageType.HEARTBEAT,
      node_id: this.nodeId,
      status: "online",
      capabilities: this.capabilities,
    };
    this.ws.send(JSON.stringify(heartbeat));
    console.log("[WindowsNode] Sent heartbeat");
  }

  private async handleCommand(cmd: NodeCommand): Promise<void> {
    console.log("[WindowsNode] Received command:", cmd);
    const startTime = Date.now();
    let result: any = null;
    let status: "ok" | "error" = "ok";
    let errorCode: string | null = null;
    let errorMessage: string | null = null;

    try {
      switch (cmd.tool) {
        case "computer.list_windows":
          result = await this.listWindows();
          break;
        case "computer.screenshot":
          result = await this.screenshot(cmd.arguments);
          break;
        case "computer.click":
          result = await this.click(cmd.arguments as { x: number; y: number; button?: string; clicks?: number; window_id?: string });
          break;
        case "computer.type":
          result = await this.type(cmd.arguments as { text: string; window_id?: string; interval_ms?: number });
          break;
        case "computer.keys":
          result = await this.keys(cmd.arguments as { keys: string | string[]; window_id?: string });
          break;
        case "computer.open_application":
          result = await this.openApplication(cmd.arguments as { application: string; arguments?: string[] });
          break;
        case "computer.close_application":
          result = await this.closeApplication(cmd.arguments);
          break;
        default:
          throw new Error(`Unknown tool: ${cmd.tool}`);
      }
    } catch (err: any) {
      status = "error";
      errorCode = "IMPLEMENTATION_NOT_READY";
      errorMessage = err.message || String(err);
      console.error("[WindowsNode] Command execution error:", err);
    }

    const durationMs = Date.now() - startTime;
    const resultMsg: NodeResult = {
      type: MessageType.RESULT,
      command_id: cmd.command_id,
      node_id: this.nodeId,
      status,
      result: status === "ok" ? result : undefined,
      error_code: errorCode,
      error: status === "error" ? errorMessage : undefined,
      duration_ms: status === "ok" ? durationMs : null,
    };

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(resultMsg));
      console.log("[WindowsNode] Sent result:", resultMsg);
    }
  }

  // --- Tool Implementations (stubs for now) ---

  async listWindows(): Promise<any> {
    // TODO: Implement actual window listing using PowerShell or native module
    // For now, return mock data
    return {
      windows: [
        { id: "1", application: "Explorer", title: "File Explorer", focused: false },
        { id: "2", application: "Code", title: "Ultron - Visual Studio Code", focused: true },
      ],
    };
  }

  async screenshot(args: { window_id?: string; name?: string }): Promise<any> {
    // TODO: Implement screenshot using robotjs or native module
    // For now, return mock path
    const name = args.name || `screenshot-${Date.now()}.png`;
    const homedir = require("os").homedir();
    const path = join(homedir, "Pictures", name);
    // In reality, we would save the screenshot to path
    return { path };
  }

  async click(args: { x: number; y: number; button?: string; clicks?: number; window_id?: string }): Promise<any> {
    // TODO: Implement mouse click using robotjs
    console.log("[WindowsNode] Click at", args.x, args.y, args.button || "left");
    return {};
  }

  async type(args: { text: string; window_id?: string; interval_ms?: number }): Promise<any> {
    // TODO: Implement keyboard typing using robotjs
    console.log("[WindowsNode] Type:", args.text);
    return {};
  }

  async keys(args: { keys: string | string[]; window_id?: string }): Promise<any> {
    // TODO: Implement key press using robotjs
    console.log("[WindowsNode] Keys:", args.keys);
    return {};
  }

  async openApplication(args: { application: string; arguments?: string[] }): Promise<any> {
    // TODO: Implement application launch
    // For now, just exec the application name (could be dangerous)
    const { application, arguments: toolArgs = [] } = args;
    return new Promise((resolve, reject) => {
      exec(`"${application}" ${toolArgs.map(a => `"${a}"`).join(" ")}`, (err, stdout, stderr) => {
        if (err) {
          reject(err);
        } else {
          resolve({ stdout, stderr });
        }
      });
    });
  }

  async closeApplication(args: { application?: string; window_id?: string }): Promise<any> {
    // TODO: Implement closing application by name or window id
    console.log("[WindowsNode] Close application:", args.application || args.window_id);
    return {};
  }
}

// Export a function to start the node
export function startWindowsNode(serverUrl: string): WindowsNode {
  const node = new WindowsNode(serverUrl);
  node.connect().catch(err => {
    console.error("[WindowsNode] Failed to start node:", err);
  });
  return node;
}
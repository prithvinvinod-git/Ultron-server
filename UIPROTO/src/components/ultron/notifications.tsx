/**
 * Notifications panel (T510, §45).
 *
 * Reads recent alerts from the event log via GET /notifications and surfaces
 * them in tiered colour by level. A badge on the rail shows unread count so
 * the operator can see "something needs attention" without opening the tab.
 *
 * A test-send section lets the operator prove the active channel on demand.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Bell, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { notificationsApi } from "@/lib/api";
import type { NotificationItem, NotificationLevel, TestNotificationRequest } from "@/lib/api";
import { queryKeys } from "@/lib/queries";

const LEVEL_CLASS: Record<NotificationLevel, string> = {
  info: "",
  warning: " warning",
  error: " failed",
  critical: " failed",
};

function shortTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("en-GB", { hour12: false });
}

export function NotificationsPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.notifications,
    queryFn: notificationsApi.list,
    refetchInterval: 15000,
  });

  const items = data ?? [];
  const unreadCount = useMemo(
    () => items.filter((i) => i.level === "warning" || i.level === "error" || i.level === "critical").length,
    [items],
  );

  const [testTitle, setTestTitle] = useState("");
  const [testMessage, setTestMessage] = useState("");
  const [testLevel, setTestLevel] = useState<NotificationLevel>("info");
  const [testBusy, setTestBusy] = useState(false);

  const sendTest = useMutation({
    mutationFn: (body: TestNotificationRequest) => notificationsApi.test(body),
    onSuccess: (result) => {
      toast.success(result.delivered ? `Delivered via ${result.provider}` : `Not delivered: ${result.detail ?? "unknown reason"}`);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "test failed");
    },
    onSettled: () => setTestBusy(false),
  });

  function handleTest() {
    setTestBusy(true);
    void sendTest.mutateAsync({
      title: testTitle || undefined,
      message: testMessage || undefined,
      level: testLevel,
    });
  }

  return (
    <section className="hud-panel">
      <div className="panel-heading">
        <h2>
          NOTIFICATIONS
          {unreadCount > 0 && (
            <span className="agent-pct">
              <Bell size={9} /> {unreadCount}
            </span>
          )}
        </h2>
        <Button
          variant="ghost"
          size="sm"
          className="panel-reload"
          onClick={() => void refetch()}
          disabled={isLoading}
          title="Refresh notifications"
        >
          <RefreshCw size={12} />
        </Button>
      </div>

      {error ? (
        <div className="panel-empty">notifications unavailable</div>
      ) : isLoading && !items.length ? (
        <div className="panel-empty">loading…</div>
      ) : !items.length ? (
        <div className="panel-empty">no notifications</div>
      ) : (
        <div className="agent-list">
          {items.map((item) => (
            <div key={item.id} className={`agent-row${LEVEL_CLASS[item.level]}`}>
              <span className={`status-dot${LEVEL_CLASS[item.level]}`} />
              <div>
                <strong>{item.title}</strong>
                <small>{item.source ?? "system"}</small>
                <p className="notif-message">{item.message}</p>
              </div>
              <span className="agent-pct">{shortTime(item.created_at)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="panel-foot">
        <span>{items.length} recent</span>
        <span>EVENT LOG</span>
      </div>

      {/* Test-send section */}
      <div className="notif-test-section">
        <div className="notif-test-label">SEND TEST</div>
        <div className="notif-test-field">
          <input
            type="text"
            placeholder="Title (optional)"
            value={testTitle}
            onChange={(e) => setTestTitle(e.target.value)}
            aria-label="Test notification title"
          />
          <input
            type="text"
            placeholder="Message (optional)"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            aria-label="Test notification message"
          />
          <select
            value={testLevel}
            onChange={(e) => setTestLevel(e.target.value as NotificationLevel)}
            aria-label="Notification level"
            className="agent-select"
          >
            <option value="info">INFO</option>
            <option value="warning">WARNING</option>
            <option value="error">ERROR</option>
            <option value="critical">CRITICAL</option>
          </select>
          <Button variant="ghost" onClick={handleTest} disabled={testBusy} size="sm">
            {testBusy ? "SENDING…" : "SEND"}
          </Button>
        </div>
      </div>
    </section>
  );
}

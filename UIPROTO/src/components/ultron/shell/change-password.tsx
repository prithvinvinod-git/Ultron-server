import { useState } from "react";
import type { FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/**
 * Change-password dialog (§29 `POST /auth/password`). The server verifies the
 * current password and revokes every other session; a wrong password comes back
 * as a 401 and is surfaced verbatim.
 */
export function ChangePasswordDialog() {
  const { changePassword } = useAuth();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setCurrent("");
    setNext("");
    setConfirm("");
    setError(null);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (next.length < 12) {
      setError("The new password must be at least 12 characters.");
      return;
    }
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await changePassword(current, next);
      toast.success("Password changed. Other sessions were signed out.");
      setOpen(false);
      reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change the password.");
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (!value) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" title="Change password" aria-label="Change password">
          <KeyRound />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={onSubmit}>
          <DialogHeader>
            <DialogTitle>Change password</DialogTitle>
            <DialogDescription>All other sessions for this account are signed out.</DialogDescription>
          </DialogHeader>
          <div className="shell-form">
            <div>
              <Label htmlFor="cp-current">Current password</Label>
              <Input
                id="cp-current"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="cp-next">New password</Label>
              <Input
                id="cp-next"
                type="password"
                autoComplete="new-password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="cp-confirm">Confirm new password</Label>
              <Input
                id="cp-confirm"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
              />
            </div>
            {error ? (
              <p className="shell-form-error" role="alert">
                {error}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="submit" disabled={pending || !current || !next}>
              {pending ? "Saving…" : "Change password"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

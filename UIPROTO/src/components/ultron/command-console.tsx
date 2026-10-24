/**
 * The command console: the console's one input bar.
 *
 * On the home page this is only the bar — submitting a prompt hands it to the
 * chat window (`onChat`), which opens and streams the answer there. The home
 * surface never renders a transcript of its own, so responses live in exactly
 * one place: the chat panel.
 */

import { useState } from "react";
import { ArrowUpRight, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";

export function CommandConsole({ onChat }: { onChat?: (prompt: string) => void } = {}) {
    const [input, setInput] = useState("");

    function send(event: React.FormEvent) {
        event.preventDefault();
        const prompt = input.trim();
        if (!prompt) return;
        onChat?.(prompt);
        setInput("");
    }

    return (
        <div className="command-search console-shell">
            <form onSubmit={send}>
                <Terminal />
                <input
                    aria-label="Ask Ultron"
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    placeholder="Ask Ultron anything…"
                />
                <Button variant="ghost" size="icon" type="submit" aria-label="Send" title="Send">
                    <ArrowUpRight />
                </Button>
            </form>
        </div>
    );
}

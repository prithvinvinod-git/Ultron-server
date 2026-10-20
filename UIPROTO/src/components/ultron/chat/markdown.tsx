/**
 * Markdown renderer for assistant messages (ported from the Ultron reference
 * repo's `chat/markdown.tsx`, adapted to ULTRON's design tokens). Keeps GFM
 * tables/lists and adds a copy button to fenced code blocks.
 */
import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";

function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = typeof children === "string" ? children : String(children ?? "");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable
    }
  };

  return (
    <div className="chat-code group/code">
      <button
        type="button"
        onClick={() => void copy()}
        className="chat-code-copy"
        aria-label="Copy code"
        title="Copy code"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? "Copied" : "Copy"}
      </button>
      <pre className={className}>{children}</pre>
    </div>
  );
}

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});

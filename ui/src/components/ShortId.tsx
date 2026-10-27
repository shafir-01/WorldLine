import { useState } from "react";
import { shortId } from "../format";

/**
 * A 7-character identifier with a copy button. The full value lives in the tooltip and is what gets copied;
 * raw sha256 strings are never printed in the default view.
 */
export function ShortId({ value, label, n = 7 }: { value: string; label?: string; n?: number }) {
  const [copied, setCopied] = useState(false);
  const copy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <span className="sid" title={value}>
      {label && <span className="sid-label">{label}</span>}
      <code className="sid-code">{shortId(value, n)}</code>
      <button type="button" className="sid-copy" onClick={copy} aria-label={`Copy full ${label ?? "id"}`} title={copied ? "Copied" : `Copy full value\n${value}`}>
        {copied ? (
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
            <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
            <rect x="5" y="5" width="8.5" height="8.5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M3 10.5V3.5A1 1 0 0 1 4 2.5h6.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        )}
      </button>
    </span>
  );
}

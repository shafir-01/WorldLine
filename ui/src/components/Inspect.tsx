import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Shared building blocks for the dense Inspect views: the question/answer intro, the sticky context bar,
 * filter chips, a keyboard-driven segmented control, an accessible tooltip and a disclosure.
 * Styles live in src/inspect.css.
 */

/** The view's one question, answered in a sentence above the fold. */
export function ViewIntro({ eyebrow, question, answer, aside }: { eyebrow: string; question: string; answer: ReactNode; aside?: ReactNode }) {
  return (
    <header className="ix-intro">
      <div className="ix-intro-text">
        <span className="ix-eyebrow">{eyebrow}</span>
        <h1 className="ix-question">{question}</h1>
        <p className="ix-answer">{answer}</p>
      </div>
      {aside && <div className="ix-intro-aside">{aside}</div>}
    </header>
  );
}

/** Sticky strip under the top bar that always says which scenario / branch / case the view is showing. */
export function ContextBar({ children, label = "Current selection" }: { children: ReactNode; label?: string }) {
  return (
    <div className="ix-context" role="region" aria-label={label}>
      <div className="ix-context-in">{children}</div>
    </div>
  );
}

export function Ctx({ k, children }: { k: string; children: ReactNode }) {
  return (
    <span className="ix-ctx">
      <span className="ix-ctx-k">{k}</span>
      <span className="ix-ctx-v">{children}</span>
    </span>
  );
}

/** Toggle chip for filters. `count` is shown muted after the label. */
export function Chip({ on, onClick, children, count, title, disabled }: { on: boolean; onClick: () => void; children: ReactNode; count?: number; title?: string; disabled?: boolean }) {
  return (
    <button type="button" className={`ix-chip ${on ? "on" : ""}`} aria-pressed={on} onClick={onClick} title={title} disabled={disabled}>
      {on && (
        <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden className="ix-chip-tick">
          <path d="M2 6.5l2.5 2.5L10 3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      <span>{children}</span>
      {count != null && <span className="ix-chip-n">{count}</span>}
    </button>
  );
}

export interface SegOption<T extends string> {
  value: T;
  label: ReactNode;
  sub?: ReactNode;
  disabled?: boolean;
  title?: string;
}

/** Segmented control as an ARIA radiogroup: arrow keys move the choice, only the checked option is in the tab order. */
export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: SegOption<T>[]; onChange: (v: T) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const enabled = options.filter((o) => !o.disabled);
  const onKey = (e: KeyboardEvent) => {
    const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    const i = enabled.findIndex((o) => o.value === value);
    const next = enabled[(i + dir + enabled.length) % enabled.length];
    onChange(next.value);
    refs.current[options.indexOf(next)]?.focus();
  };
  return (
    <div className="ix-seg" role="radiogroup" aria-label={label} onKeyDown={onKey}>
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => (refs.current[i] = el)}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          tabIndex={o.value === value ? 0 : -1}
          disabled={o.disabled}
          title={o.title}
          className={`ix-seg-btn ${o.value === value ? "on" : ""}`}
          onClick={() => onChange(o.value)}
        >
          <span className="ix-seg-l">{o.label}</span>
          {o.sub != null && <span className="ix-seg-sub">{o.sub}</span>}
        </button>
      ))}
    </div>
  );
}

/**
 * Hover/focus tooltip. The trigger is a real button (keyboard reachable); the bubble is portalled to <body>
 * so overflow containers and sticky bars never clip it. Esc closes it.
 */
export function Tip({ text, children, label, className = "" }: { text: ReactNode; children?: ReactNode; label?: string; className?: string }) {
  const id = useId();
  const ref = useRef<HTMLButtonElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number; below: boolean } | null>(null);
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const below = r.top < 140;
    setPos({ x: Math.min(Math.max(r.left + r.width / 2, 170), window.innerWidth - 170), y: below ? r.bottom + 8 : r.top - 8, below });
  };
  const hide = () => setPos(null);
  return (
    <>
      <button
        ref={ref}
        type="button"
        className={`ix-tip-trigger ${children ? "has-text" : ""} ${className}`}
        aria-describedby={pos ? id : undefined}
        aria-label={children ? undefined : label ?? "More information"}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onKeyDown={(e) => e.key === "Escape" && hide()}
        onClick={(e) => (e.stopPropagation(), pos ? hide() : show())}
      >
        {children ?? (
          <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden>
            <circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M8 7.2v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            <circle cx="8" cy="4.9" r="0.95" fill="currentColor" />
          </svg>
        )}
      </button>
      {pos &&
        createPortal(
          <div id={id} role="tooltip" className={`ix-tip ${pos.below ? "below" : ""}`} style={{ left: pos.x, top: pos.y }}>
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}

/** Floating card that follows a point (used for chart hover cards). Portalled, clamped to the viewport. */
export function HoverCard({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [p, setP] = useState({ left: x + 14, top: y + 14 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = x + 16 + w > window.innerWidth - 8 ? x - 16 - w : x + 16;
    const top = y + 16 + h > window.innerHeight - 48 ? Math.max(8, y - 16 - h) : y + 16;
    setP({ left, top });
  }, [x, y]);
  return createPortal(
    <div ref={ref} className="ix-hovercard" style={p} role="status">
      {children}
    </div>,
    document.body,
  );
}

/** Collapsible section with a consistent look. Uses native <details> so it is keyboard and screen-reader friendly by default. */
export function Disclosure({ summary, meta, children, defaultOpen, open, onToggle, className = "" }: { summary: ReactNode; meta?: ReactNode; children: ReactNode; defaultOpen?: boolean; open?: boolean; onToggle?: (open: boolean) => void; className?: string }) {
  const controlled = open !== undefined;
  return (
    <details className={`ix-disc ${className}`} open={controlled ? open : defaultOpen} onToggle={(e) => onToggle?.((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <svg className="ix-disc-caret" width="12" height="12" viewBox="0 0 12 12" aria-hidden>
          <path d="M4 2.5L7.5 6 4 9.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="ix-disc-title">{summary}</span>
        {meta && <span className="ix-disc-meta">{meta}</span>}
      </summary>
      <div className="ix-disc-body">{children}</div>
    </details>
  );
}

/** A checklist line: tick or cross, a plain label, an optional technical term and a tooltip explaining it. */
export function Check({ ok, label, term, help }: { ok: boolean; label: ReactNode; term?: string; help?: ReactNode }) {
  return (
    <li className={`ix-check ${ok ? "ok" : "nok"}`}>
      <span className="ix-check-mark" aria-hidden>
        {ok ? (
          <svg width="16" height="16" viewBox="0 0 16 16">
            <circle cx="8" cy="8" r="7" fill="var(--pass)" />
            <path d="M4.8 8.3l2.1 2.1 4.3-4.6" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 16 16">
            <path d="M8 1L15 8 8 15 1 8z" fill="var(--fail)" />
            <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        )}
      </span>
      <span className="sr-only">{ok ? "Met:" : "Not met:"}</span>
      <span className="ix-check-l">
        {label}
        {term && (
          <code className="ix-term" title={term}>
            {term}
          </code>
        )}
      </span>
      {help && <Tip text={help} label={`What does this mean?`} />}
    </li>
  );
}

/**
 * App's router scrolls to the top on every hash change (fine for switching views, jarring when a row click
 * only changes the selected branch). Run the navigation, then put the page back where it was.
 */
export function keepScroll(fn: () => void) {
  const y = window.scrollY;
  fn();
  window.scrollTo(0, y);
  requestAnimationFrame(() => window.scrollTo(0, y));
}

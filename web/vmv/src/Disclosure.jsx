import { useState } from "react";

export function Disclosure({ title, children, section = false }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="disclosure">
      <button
        type="button"
        className={section ? "disclosure-toggle section" : "disclosure-toggle"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        {title}
      </button>
      {open ? <div className="disclosure-body">{children}</div> : null}
    </div>
  );
}

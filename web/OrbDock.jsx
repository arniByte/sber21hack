import React, { useEffect, useRef, useState } from "react";

export default function OrbDock({ items, active, onNavigate, disabled }) {
  const [open, setOpen] = useState(false);
  const dock = useRef(null);
  const sphere = useRef(null);
  const menu = useRef(null);

  useEffect(() => {
    if (!open) return;
    const outside = (event) => {
      if (!dock.current.contains(event.target)) setOpen(false);
    };
    const escape = (event) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      sphere.current.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return <div ref={dock} className="orb-dock" data-open={open}>
    <button ref={sphere} type="button" className="dock-sphere" aria-expanded={open}
      aria-controls="cyclone-dock-menu" aria-label={open ? "Закрыть меню" : "Открыть меню"}
      onClick={() => setOpen(!open)}
      onKeyDown={(event) => {
        if (event.key !== "ArrowUp" || disabled) return;
        event.preventDefault();
        setOpen(true);
        requestAnimationFrame(() => menu.current.querySelector("[aria-current]")?.focus());
      }}>
      <i aria-hidden="true" />
    </button>
    <nav ref={menu} id="cyclone-dock-menu" className="dock-panel" aria-label="Разделы" inert={!open} aria-hidden={!open}>
      {items.map(([key, label]) => <button type="button" key={key} disabled={disabled}
        aria-current={active === key ? "page" : undefined} onClick={() => {
          sphere.current.focus();
          setOpen(false);
          onNavigate(key);
        }}>{label}</button>)}
    </nav>
  </div>;
}

"use client";

import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

export function useSort<T>(rows: T[], initialKey: keyof T, initialDir: SortDir = "desc") {
  const [key, setKey] = useState<keyof T>(initialKey);
  const [dir, setDir] = useState<SortDir>(initialDir);

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      let cmp = 0;
      if (typeof av === "number" && typeof bv === "number") cmp = av - bv;
      else cmp = String(av ?? "").localeCompare(String(bv ?? ""));
      return dir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, key, dir]);

  const toggle = (k: keyof T) => {
    if (k === key) setDir(dir === "asc" ? "desc" : "asc");
    else {
      setKey(k);
      setDir(typeof rows[0]?.[k] === "number" ? "desc" : "asc");
    }
  };

  return { sorted, key, dir, toggle };
}

export function SortHeader<T>({
  label, col, active, dir, onClick, align = "right", className = "",
}: {
  label: string; col: keyof T; active: keyof T; dir: SortDir; onClick: (k: keyof T) => void; align?: "left" | "right" | "center"; className?: string;
}) {
  const isActive = active === col;
  return (
    <th className={`py-3 px-3 text-${align} ${className}`}>
      <button
        type="button"
        onClick={() => onClick(col)}
        className={`inline-flex items-center gap-1 uppercase tracking-wider font-semibold hover:text-slate-200 transition ${isActive ? "text-cyan-300" : ""}`}
        title={`Sort by ${label}`}
      >
        {label}
        <span className="text-[9px] opacity-70">{isActive ? (dir === "asc" ? "▲" : "▼") : "⇅"}</span>
      </button>
    </th>
  );
}

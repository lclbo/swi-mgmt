import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import { FrontPanelView } from "./FrontPanelView";
import type {
  AppConfig,
  PortStatus,
  ScanResult,
  ScenarioDocument,
  ScenarioImportMode,
  SessionState,
  SwitchConfig,
  SwitchOrder,
  SwitchSnapshot,
  VlanConflict,
} from "./types";

type Tab = "ports" | "vlans" | "matrix" | "live" | "panel";
type OperFilter = "all" | "up" | "down";
type RoleFilter = "all" | "trunk" | "access";
type Theme = "light" | "dark";

const THEME_KEY = "swi-mgmt-theme";

function readStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* ignore */
  }
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* ignore */
  }
}

interface SwitchHealth {
  status: "unknown" | "ok" | "error" | "loading";
  lastOkAt: number | null;
  lastError: string;
  upPorts: number;
  totalPorts: number;
}

const POLL_INTERVAL_PRESETS = [0.5, 1, 2, 5, 10, 15, 30, 60] as const;

function formatPollInterval(sec: number): string {
  const n = Number(sec);
  if (!Number.isFinite(n)) return "—";
  return `${n}s`;
}

/** Wall time of a completed live poll cycle (ms → short label). */
function formatPollDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const sec = ms / 1000;
  if (sec < 10) return `${sec.toFixed(1)}s`;
  return `${sec.toFixed(0)}s`;
}

function placeFixedPopover(
  anchor: DOMRect,
  popW: number,
  popH: number
): { left: number; top: number } {
  const gap = 6;
  const margin = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Prefer below the button; flip above if it would leave the viewport.
  let top = anchor.bottom + gap;
  if (top + popH > vh - margin) {
    top = anchor.top - gap - popH;
  }
  top = Math.min(Math.max(margin, top), Math.max(margin, vh - margin - popH));

  // Align to the right edge of the button, then clamp horizontally.
  let left = anchor.right - popW;
  left = Math.min(Math.max(margin, left), Math.max(margin, vw - margin - popW));

  return { left, top };
}

function ErrorPopover({
  message,
  open,
  onClose,
  anchorRef,
}: {
  message: string;
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onPointer = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t)) return;
      if (anchorRef.current?.contains(t)) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointer);
    };
  }, [open, onClose, anchorRef]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchorEl = anchorRef.current;
      const popEl = popoverRef.current;
      if (!anchorEl || !popEl) return;
      const anchor = anchorEl.getBoundingClientRect();
      const { width, height } = popEl.getBoundingClientRect();
      setPos(placeFixedPopover(anchor, width || 320, height || 160));
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, message, anchorRef]);

  if (!open) return null;

  return (
    <div
      className="switch-error-popover"
      role="dialog"
      aria-label="Error details"
      ref={popoverRef}
      style={{ left: pos.left, top: pos.top }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="error-popover-head">
        <span>Last error</span>
        <button type="button" className="icon-btn" onClick={onClose} title="Dismiss">
          ✕
        </button>
      </div>
      <textarea
        className="error-popover-detail"
        readOnly
        value={message}
        rows={Math.min(12, Math.max(3, message.split("\n").length + 1))}
        onFocus={(e) => e.target.select()}
      />
    </div>
  );
}

function SwitchErrorButton({ message }: { message: string }) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setOpen(false);
  }, [message]);

  return (
    <div className="switch-error-wrap">
      <button
        ref={btnRef}
        type="button"
        className="icon-btn switch-error-btn"
        title="Show last error"
        aria-label="Show last error"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        !
      </button>
      <ErrorPopover
        message={message}
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={btnRef}
      />
    </div>
  );
}

function formatRate(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`;
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(1)} Kbps`;
  return `${bps.toFixed(0)} bps`;
}

function formatAge(ts: number | null, now: number): string {
  if (ts == null) return "never";
  const sec = Math.max(0, Math.floor((now - ts) / 1000));
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  return `${Math.floor(min / 60)}h ago`;
}

function portUntaggedVlans(port: PortStatus): number[] {
  if (port.untagged_vlans?.length) return port.untagged_vlans;
  if (port.tagged_vlans.includes(port.primary_vlan)) return [];
  return [port.primary_vlan];
}

function portVlanIds(port: PortStatus): number[] {
  return [...new Set([...portUntaggedVlans(port), ...port.tagged_vlans])];
}

function portHasVlan(port: PortStatus, vlanId: number): boolean {
  return portUntaggedVlans(port).includes(vlanId) || port.tagged_vlans.includes(vlanId);
}

function isTrunk(port: PortStatus): boolean {
  return port.tagged_vlans.length > 0 || portVlanIds(port).length > 1;
}

function portLinkState(port: PortStatus): "up" | "down" | "admin-down" {
  if (port.admin_status === "DOWN") return "admin-down";
  if (port.oper_status === "UP") return "up";
  return "down";
}

function PortBeacon({ port }: { port: PortStatus }) {
  const state = portLinkState(port);
  const label =
    state === "admin-down"
      ? "Admin down"
      : state === "up"
        ? "Oper up"
        : "Oper down";
  return (
    <span
      className={`port-beacon port-beacon-${state}`}
      title={label}
      aria-label={label}
    />
  );
}

function vlanLabel(session: SessionState, id: number): string {
  const v = session.vlans.find((x) => x.vlan_id === id);
  return v?.name ? `${id} ${v.name}` : String(id);
}

function vlanColor(session: SessionState, id: number): string {
  return session.vlans.find((x) => x.vlan_id === id)?.color || "#64748b";
}

function vlanNameOnly(session: SessionState, id: number): string {
  return session.vlans.find((x) => x.vlan_id === id)?.name || "";
}

function VlanPill({
  vlanId,
  role,
  session,
}: {
  vlanId: number;
  role: "U" | "T";
  session: SessionState;
}) {
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const color = vlanColor(session, vlanId);
  const name = vlanNameOnly(session, vlanId);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const el = anchorRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const tipW = 240;
      const tipH = 56;
      let left = r.left + r.width / 2 - tipW / 2;
      let top = r.top - tipH - 8;
      if (top < 8) top = r.bottom + 8;
      left = Math.min(Math.max(8, left), window.innerWidth - tipW - 8);
      setPos({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, name, vlanId]);

  return (
    <>
      <span
        ref={anchorRef}
        className={`vlan-pill ${role === "U" ? "untagged" : "tagged"}`}
        style={
          role === "U"
            ? { background: color, color: "var(--vlan-on-color)" }
            : {
                borderColor: color,
                background: `color-mix(in srgb, ${color} 28%, transparent)`,
              }
        }
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {role === "T" && (
          <span className="vlan-pill-swatch" style={{ background: color }} />
        )}
        <span className="vlan-pill-id">{vlanId}</span>
        <span className="vlan-pill-role">{role}</span>
      </span>
      {open &&
        createPortal(
          <div
            className="vlan-pill-float"
            role="tooltip"
            style={{ left: pos.left, top: pos.top }}
          >
            <span className="vlan-pill-float-swatch" style={{ background: color }} />
            <div className="vlan-pill-float-text">
              <strong>VLAN {vlanId}</strong>
              <span className="vlan-pill-float-name">{name || "Unnamed"}</span>
              <span className="vlan-pill-float-role">
                {role === "U" ? "Untagged" : "Tagged"}
              </span>
            </div>
          </div>,
          document.body
        )}
    </>
  );
}

function VlanPillList({
  ids,
  role,
  session,
}: {
  ids: number[];
  role: "U" | "T";
  session: SessionState;
}) {
  if (ids.length === 0) return <span className="vlan-pill-empty">—</span>;
  return (
    <span className="vlan-pill-row">
      {ids.map((id) => (
        <VlanPill key={`${role}-${id}`} vlanId={id} role={role} session={session} />
      ))}
    </span>
  );
}

function formatVlanChips(
  port: PortStatus,
  session: SessionState,
  max = 3
): { parts: string[]; more: number } {
  const parts: string[] = [];
  for (const vid of portUntaggedVlans(port)) {
    parts.push(`${vlanLabel(session, vid)} U`);
  }
  for (const vid of port.tagged_vlans) {
    if (!portUntaggedVlans(port).includes(vid)) {
      parts.push(`${vlanLabel(session, vid)} T`);
    }
  }
  if (parts.length <= max) return { parts, more: 0 };
  return { parts: parts.slice(0, max), more: parts.length - max };
}

function formatAllVlans(port: PortStatus, session: SessionState): string {
  const { parts, more } = formatVlanChips(port, session, 99);
  return more ? `${parts.join(", ")} +${more} more` : parts.join(", ");
}

function filterPorts(
  ports: PortStatus[],
  opts: {
    vlanFilter: number | null;
    operFilter: OperFilter;
    roleFilter: RoleFilter;
    search: string;
  }
): PortStatus[] {
  const q = opts.search.trim().toLowerCase();
  return ports.filter((p) => {
    if (opts.vlanFilter != null && !portHasVlan(p, opts.vlanFilter)) return false;
    if (opts.operFilter === "up" && p.oper_status !== "UP") return false;
    if (opts.operFilter === "down" && p.oper_status === "UP") return false;
    if (opts.roleFilter === "trunk" && !isTrunk(p)) return false;
    if (opts.roleFilter === "access" && isTrunk(p)) return false;
    if (q && !p.name.toLowerCase().includes(q)) return false;
    return true;
  });
}

function VlanList({
  session,
  onSelect,
}: {
  session: SessionState;
  onSelect: (vlanId: number) => void;
}) {
  return (
    <div className="panel vlan-panel">
      <div className="panel-title">
        <span>Session VLANs</span>
      </div>
      <div className="panel-body">
        {session.vlans.length === 0 ? (
          <div className="empty-hint">VLANs appear after you load a switch.</div>
        ) : (
          session.vlans.map((v) => (
            <div
              key={v.vlan_id}
              className={`vlan-item ${session.highlight_vlan === v.vlan_id ? "selected" : ""}`}
              onClick={() => onSelect(v.vlan_id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelect(v.vlan_id);
              }}
            >
              <div className="vlan-swatch" style={{ background: v.color }} />
              <div>
                <div>
                  <strong>VLAN {v.vlan_id}</strong> {v.name || "—"}
                </div>
                <div
                  className="vlan-meta"
                  title={`${v.untagged_count} untagged · ${v.tagged_count} tagged`}
                >
                  {v.port_count} ({v.untagged_count}/{v.tagged_count}) ports
                </div>
              </div>
            </div>
          ))
        )}
        {session.vlans.length > 0 && (
          <div className="vlan-meta hint">
            Click a VLAN to filter ports. Click again to clear.
          </div>
        )}
      </div>
    </div>
  );
}

function PortFilters({
  search,
  operFilter,
  roleFilter,
  onSearch,
  onOper,
  onRole,
}: {
  search: string;
  operFilter: OperFilter;
  roleFilter: RoleFilter;
  onSearch: (v: string) => void;
  onOper: (v: OperFilter) => void;
  onRole: (v: RoleFilter) => void;
}) {
  return (
    <div className="filters">
      <input
        className="filter-search"
        placeholder="Search port…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
      />
      <select value={operFilter} onChange={(e) => onOper(e.target.value as OperFilter)}>
        <option value="all">All status</option>
        <option value="up">UP only</option>
        <option value="down">DOWN only</option>
      </select>
      <select value={roleFilter} onChange={(e) => onRole(e.target.value as RoleFilter)}>
        <option value="all">All roles</option>
        <option value="trunk">Trunks</option>
        <option value="access">Access</option>
      </select>
    </div>
  );
}

function PortTable({
  ports,
  session,
  showMatrix,
  showRates,
}: {
  ports: PortStatus[];
  session: SessionState;
  showMatrix: boolean;
  /** False in fast mode — traffic counters are not polled. */
  showRates: boolean;
}) {
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);

  if (ports.length === 0) {
    return <div className="empty-hint">No ports match the current filters.</div>;
  }

  if (showMatrix) {
    const colHover = (col: number) => (hover?.col === col ? " matrix-col-hover" : "");
    const setCellHover = (row: number, col: number) => setHover({ row, col });

    return (
      <div className="table-wrap" onMouseLeave={() => setHover(null)}>
        <table className="matrix-table">
          <thead>
            <tr>
              <th
                className={`sticky-col${colHover(0)}`}
                onMouseEnter={() => setCellHover(-1, 0)}
              >
                Port
              </th>
              {session.vlans.map((v, vi) => (
                <th
                  key={v.vlan_id}
                  className={`matrix-head${colHover(vi + 1)}`}
                  style={{ background: v.color, color: "#0f172a" }}
                  title={v.name || String(v.vlan_id)}
                  onMouseEnter={() => setCellHover(-1, vi + 1)}
                >
                  {v.vlan_id}
                  <br />
                  <small>{(v.name || "").slice(0, 8)}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ports.map((port, ri) => {
              const untagged = portUntaggedVlans(port);
              const rowHover = hover?.row === ri ? " matrix-row-hover" : "";
              return (
                <tr key={port.index} className={rowHover.trim()}>
                  <td
                    className={`sticky-col port-label${colHover(0)}`}
                    onMouseEnter={() => setCellHover(ri, 0)}
                  >
                    <PortBeacon port={port} />
                    <strong>{port.name}</strong>
                  </td>
                  {session.vlans.map((v, vi) => {
                    const isU = untagged.includes(v.vlan_id);
                    const isT = port.tagged_vlans.includes(v.vlan_id) && !isU;
                    return (
                      <td
                        key={v.vlan_id}
                        className={`${isU ? "vlan-cell-u" : isT ? "vlan-cell-t" : ""}${colHover(vi + 1)}`.trim()}
                        style={
                          isU || isT
                            ? { background: v.color, color: "#0f172a" }
                            : undefined
                        }
                        onMouseEnter={() => setCellHover(ri, vi + 1)}
                      >
                        {isU ? "U" : isT ? "T" : ""}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table className="compact-table">
        <thead>
          <tr>
            <th className="sticky-col">Port</th>
            <th>Speed</th>
            <th>Native</th>
            <th>Tagged</th>
            {showRates && (
              <>
                <th>In</th>
                <th>Out</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {ports.map((port) => {
            const untagged = portUntaggedVlans(port);
            const tagged = port.tagged_vlans.filter((v) => !untagged.includes(v));
            return (
              <tr key={port.index}>
                <td className="sticky-col port-label">
                  <PortBeacon port={port} />
                  <strong>{port.name}</strong>
                  {isTrunk(port) && <span className="badge trunk">trunk</span>}
                </td>
                <td>{port.speed_mbps ? `${port.speed_mbps}` : "—"}</td>
                <td>
                  <VlanPillList ids={untagged} role="U" session={session} />
                </td>
                <td>
                  <VlanPillList ids={tagged} role="T" session={session} />
                </td>
                {showRates && (
                  <>
                    <td>{port.in_rate_bps ? formatRate(port.in_rate_bps) : "—"}</td>
                    <td>{port.out_rate_bps ? formatRate(port.out_rate_bps) : "—"}</td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VlanTable({
  snapshot,
  session,
  vlanFilter,
}: {
  snapshot: SwitchSnapshot;
  session: SessionState;
  vlanFilter: number | null;
}) {
  const untagged: Record<number, string[]> = {};
  const tagged: Record<number, string[]> = {};
  for (const port of snapshot.ports) {
    for (const vid of portUntaggedVlans(port)) {
      (untagged[vid] ??= []).push(port.name);
    }
    for (const vid of port.tagged_vlans) {
      (tagged[vid] ??= []).push(port.name);
    }
  }

  const vlans =
    vlanFilter != null
      ? snapshot.vlans.filter((v) => v.vlan_id === vlanFilter)
      : snapshot.vlans;

  if (vlans.length === 0) {
    return <div className="empty-hint">No VLANs to show.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>VLAN ID</th>
            <th>Name</th>
            <th>Ports (Untagged)</th>
            <th>Ports (Tagged)</th>
          </tr>
        </thead>
        <tbody>
          {vlans.map((vlan) => {
            const color =
              session.vlans.find((v) => v.vlan_id === vlan.vlan_id)?.color || "#64748b";
            const uList = untagged[vlan.vlan_id] || [];
            const tList = tagged[vlan.vlan_id] || [];
            const fmt = (list: string[]) =>
              list.length <= 12
                ? list.join(", ") || "—"
                : `${list.slice(0, 12).join(", ")} +${list.length - 12} more`;
            return (
              <tr key={vlan.vlan_id}>
                <td style={{ background: color, color: "#0f172a" }}>{vlan.vlan_id}</td>
                <td>{vlan.name || "—"}</td>
                <td title={uList.join(", ")}>{fmt(uList)}</td>
                <td title={tList.join(", ")}>{fmt(tList)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LiveIntervalControl({
  live,
  valueSec,
  lastPollMs,
  title,
  onChange,
}: {
  live: boolean;
  valueSec: number;
  /** Duration of the last completed live poll cycle, or null if none yet. */
  lastPollMs: number | null;
  title: string;
  onChange: (sec: number) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: 0, top: 0 });

  useEffect(() => {
    if (!live) setOpen(false);
  }, [live]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onPointer = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t)) return;
      if (anchorRef.current?.contains(t)) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchorEl = anchorRef.current;
      const popEl = popoverRef.current;
      if (!anchorEl || !popEl) return;
      const anchor = anchorEl.getBoundingClientRect();
      const { width, height } = popEl.getBoundingClientRect();
      setPos(placeFixedPopover(anchor, width || 220, height || 160));
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, lastPollMs]);

  const apply = async (sec: number) => {
    const clamped = Math.max(0.5, Math.round(sec * 10) / 10);
    if (!Number.isFinite(clamped)) return;
    setBusy(true);
    try {
      await onChange(clamped);
      setOpen(false);
      setCustom("");
    } finally {
      setBusy(false);
    }
  };

  const label = formatPollInterval(valueSec);
  const lastPollLabel =
    lastPollMs != null ? formatPollDuration(lastPollMs) : null;

  return (
    <>
      <button
        type="button"
        ref={anchorRef}
        className={`live-interval${live ? " visible" : ""}${open ? " open" : ""}`}
        disabled={!live}
        aria-hidden={!live}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={live ? `${title} — click to change` : undefined}
        onClick={() => {
          if (!live) return;
          setOpen((v) => !v);
          setCustom(String(valueSec));
        }}
      >
        {label}
      </button>
      {open && (
        <div
          className="live-interval-popover"
          role="dialog"
          aria-label="Live poll interval"
          ref={popoverRef}
          style={{ left: pos.left, top: pos.top }}
          onClick={(e) => e.stopPropagation()}
        >
          <div
            className="live-interval-last"
            title="Wall time from starting the last live refresh until the final reply arrived"
          >
            <span className="live-interval-last-label">Last poll took</span>
            <strong className="live-interval-last-value">
              {lastPollLabel ?? "…"}
            </strong>
          </div>
          <div className="live-interval-popover-title">Pause after each poll</div>
          <div className="live-interval-presets">
            {POLL_INTERVAL_PRESETS.map((sec) => (
              <button
                key={sec}
                type="button"
                className={Number(valueSec) === sec ? "primary" : ""}
                disabled={busy}
                onClick={() => void apply(sec)}
              >
                {formatPollInterval(sec)}
              </button>
            ))}
          </div>
          <form
            className="live-interval-custom"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(custom);
              if (Number.isFinite(n)) void apply(n);
            }}
          >
            <input
              type="number"
              min={0.5}
              step={0.5}
              inputMode="decimal"
              value={custom}
              disabled={busy}
              aria-label="Custom interval seconds"
              onChange={(e) => setCustom(e.target.value)}
            />
            <button type="submit" className="primary" disabled={busy}>
              Set
            </button>
          </form>
        </div>
      )}
    </>
  );
}

function LiveView({
  ports,
  session,
  showRates,
}: {
  ports: PortStatus[];
  session: SessionState;
  /** False in fast mode — traffic counters are not polled on structure refresh. */
  showRates: boolean;
}) {
  const maxRate = Math.max(
    1_000_000,
    ...ports.flatMap((p) => [p.in_rate_bps, p.out_rate_bps]),
    1
  );

  const sorted = useMemo(() => {
    return [...ports].sort((a, b) => {
      const na = parseInt(a.name, 10);
      const nb = parseInt(b.name, 10);
      if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
      return a.name.localeCompare(b.name, undefined, { numeric: true });
    });
  }, [ports]);

  if (sorted.length === 0) {
    return <div className="empty-hint">No ports match the current filters.</div>;
  }

  const cols = sorted.length > 36 ? 12 : sorted.length > 16 ? 8 : 4;

  return (
    <div className="port-panel" style={{ ["--cols" as string]: cols }}>
      {sorted.map((port) => {
        const primaryColor =
          session.vlans.find((v) => v.vlan_id === port.primary_vlan)?.color || "#64748b";
        const trunk = isTrunk(port);
        const inPct = Math.min(100, (port.in_rate_bps / maxRate) * 100);
        const outPct = Math.min(100, (port.out_rate_bps / maxRate) * 100);
        const { parts, more } = formatVlanChips(port, session, 2);

        return (
          <div
            key={port.index}
            className={`port-card ${port.oper_status === "UP" ? "up" : "down"}`}
            style={{ borderLeftColor: primaryColor }}
            title={formatAllVlans(port, session)}
          >
            <div className="port-card-top">
              <span className="name">{port.name}</span>
              {trunk && <span className="badge trunk">T</span>}
            </div>
            <div className={`status ${port.oper_status === "UP" ? "oper-up" : "oper-down"}`}>
              {port.oper_status}
              {port.speed_mbps ? ` · ${port.speed_mbps}` : ""}
            </div>
            <div className="native" style={{ color: primaryColor }}>
              U {vlanLabel(session, port.primary_vlan)}
            </div>
            <div className="vlans">
              {parts.join(", ")}
              {more > 0 ? ` +${more}` : ""}
            </div>
            {showRates && (
              <div className="rates">
                <div>↓ {formatRate(port.in_rate_bps)}</div>
                <div className="rate-bar">
                  <div style={{ width: `${inPct}%` }} />
                </div>
                <div>↑ {formatRate(port.out_rate_bps)}</div>
                <div className="rate-bar">
                  <div style={{ width: `${outPct}%` }} />
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ConflictModal({
  conflicts,
  onResolve,
  onResolveAll,
}: {
  conflicts: VlanConflict[];
  onResolve: (vlanId: number, choice: "session" | "switch") => void;
  onResolveAll: (choice: "session" | "switch") => void;
}) {
  const current = conflicts[0];
  if (!current) return null;
  const hasMany = conflicts.length > 1;

  return (
    <div className="modal-backdrop">
      <div className="modal conflict-modal">
        <h2>
          VLAN name conflict
          {hasMany ? `s (${conflicts.length})` : ` — VLAN ${current.vlan_id}`}
        </h2>
        <p className="conflict-lead">
          {hasMany ? (
            <>
              Resolve the highlighted row, or apply one choice to every conflict listed
              below.
            </>
          ) : (
            <>
              Switch <strong>{current.switch_label}</strong> reports a different name than
              the session for VLAN {current.vlan_id}.
            </>
          )}
        </p>

        <div className="conflict-table-wrap">
          <table className="conflict-table">
            <thead>
              <tr>
                <th>VLAN</th>
                <th>Session name</th>
                <th>Switch name</th>
                <th>Switch</th>
              </tr>
            </thead>
            <tbody>
              {conflicts.map((c, i) => (
                <tr
                  key={`${c.vlan_id}-${c.switch_host}-${i}`}
                  className={i === 0 ? "current" : undefined}
                >
                  <td className="conflict-vlan">{c.vlan_id}</td>
                  <td>{c.session_name || "—"}</td>
                  <td>{c.switch_name || "—"}</td>
                  <td className="conflict-switch" title={c.switch_host}>
                    {c.switch_label || c.switch_host}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {hasMany && (
          <p className="vlan-meta conflict-hint">
            Highlighted row is next: VLAN {current.vlan_id} on {current.switch_label}
          </p>
        )}

        <div className="form-actions">
          <button onClick={() => onResolve(current.vlan_id, "session")}>
            Keep session name
          </button>
          <button className="primary" onClick={() => onResolve(current.vlan_id, "switch")}>
            Use switch name
          </button>
        </div>
        {hasMany && (
          <div className="form-actions secondary">
            <button onClick={() => onResolveAll("session")}>Keep session for all</button>
            <button onClick={() => onResolveAll("switch")}>Use switch for all</button>
          </div>
        )}
      </div>
    </div>
  );
}

function downloadScenarioJson(doc: ScenarioDocument) {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const base = (doc.name || "scenario").trim().replace(/[^\w.-]+/g, "_") || "scenario";
  const blob = new Blob([JSON.stringify(doc, null, 2) + "\n"], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `swi-mgmt-${base}-${stamp}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function ScenarioDialog({
  currentSwitchCount,
  onClose,
  onImported,
}: {
  currentSwitchCount: number;
  onClose: () => void;
  onImported: (config: AppConfig, message: string) => void;
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<ScenarioImportMode>("replace");
  const [preview, setPreview] = useState<ScenarioDocument | null>(null);
  const [fileLabel, setFileLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleExport = async () => {
    setBusy(true);
    setError("");
    try {
      const doc = await api.exportScenario(name.trim());
      downloadScenarioJson(doc);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleExportXlsx = async () => {
    setBusy(true);
    setError("");
    try {
      await api.exportVlanMatrixXlsx();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleFile = async (file: File | null) => {
    setError("");
    setPreview(null);
    setFileLabel("");
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text) as ScenarioDocument;
      if (!data || typeof data !== "object" || !Array.isArray(data.switches)) {
        throw new Error("File must be a scenario JSON with a switches array");
      }
      setPreview(data);
      setFileLabel(file.name);
      if (!name.trim() && data.name) setName(String(data.name));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleImport = async () => {
    if (!preview) return;
    const count = preview.switches.length;
    const label = name.trim() || preview.name || fileLabel || "scenario";
    const confirmMsg =
      mode === "replace"
        ? `Replace the current inventory (${currentSwitchCount} switch${
            currentSwitchCount === 1 ? "" : "es"
          }) with “${label}” (${count} switch${count === 1 ? "" : "es"}) and its SNMP settings?`
        : `Merge “${label}” (${count} switch${
            count === 1 ? "" : "es"
          }) into the current inventory? Existing hosts with the same IP are updated.`;
    if (!window.confirm(confirmMsg)) return;

    setBusy(true);
    setError("");
    try {
      const payload = name.trim() ? { ...preview, name: name.trim() } : preview;
      const res = await api.importScenario(payload, mode);
      const s = res.summary;
      const msg =
        mode === "replace"
          ? `Imported “${s.name || label}”: ${s.switches} switch${s.switches === 1 ? "" : "es"}`
          : `Merged “${s.name || label}”: +${s.added} added, ${s.updated} updated (${s.switches} total)`;
      onImported(res.config, msg);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Scenarios</h2>
        <p className="vlan-meta">
          Export or import switch lists and SNMP settings (communities, versions, scan defaults,
          poll interval).
        </p>
        <div className="form-row">
          <label>Scenario name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Optional label"
            disabled={busy}
          />
        </div>

        <div className="scenario-section">
          <div className="scenario-section-title">Export</div>
          <div className="scenario-export-row">
            <button type="button" className="primary" disabled={busy} onClick={() => void handleExport()}>
              Download scenario JSON
            </button>
            <button
              type="button"
              disabled={busy || currentSwitchCount === 0}
              title="VLAN matrix for all switches in one Excel sheet"
              onClick={() => void handleExportXlsx()}
            >
              Export .xlsx
            </button>
          </div>
        </div>

        <div className="scenario-section">
          <div className="scenario-section-title">Import</div>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="sr-only"
            onChange={(e) => void handleFile(e.target.files?.[0] ?? null)}
          />
          <div className="scenario-import-row">
            <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
              Choose file…
            </button>
            <span className="vlan-meta">{fileLabel || "No file selected"}</span>
          </div>
          {preview && (
            <div className="scenario-preview">
              <div>
                {preview.switches.length} switch{preview.switches.length === 1 ? "" : "es"}
                {preview.settings ? " · includes SNMP settings" : ""}
                {preview.exported_at ? ` · exported ${preview.exported_at.slice(0, 10)}` : ""}
              </div>
              <div className="scenario-mode">
                <label>
                  <input
                    type="radio"
                    name="scenario-mode"
                    checked={mode === "replace"}
                    onChange={() => setMode("replace")}
                    disabled={busy}
                  />
                  Replace current inventory
                </label>
                <label>
                  <input
                    type="radio"
                    name="scenario-mode"
                    checked={mode === "merge"}
                    onChange={() => setMode("merge")}
                    disabled={busy}
                  />
                  Merge (add / update by IP)
                </label>
              </div>
              <button type="button" className="primary" disabled={busy} onClick={() => void handleImport()}>
                Import scenario
              </button>
            </div>
          )}
        </div>

        {error && <p className="error-banner">{error}</p>}
        <div className="form-actions">
          <button type="button" onClick={onClose} disabled={busy}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function SwitchDialog({
  initial,
  onSave,
  onClose,
  onDelete,
}: {
  initial?: SwitchConfig;
  onSave: (cfg: SwitchConfig) => Promise<void>;
  onClose: () => void;
  onDelete?: () => Promise<void>;
}) {
  const [form, setForm] = useState<SwitchConfig>(
    initial || {
      host: "",
      community: "public",
      snmp_version: 2,
      name: "",
      driver_id: "",
      port: 161,
      v3_user: "",
      v3_auth_proto: "sha",
      v3_auth_key: "",
      v3_priv_proto: "aes128",
      v3_priv_key: "",
    }
  );
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saveError, setSaveError] = useState("");

  const busy = saving || deleting;
  const isV3 = form.snmp_version === 3;

  const handleSave = async () => {
    setSaving(true);
    setSaveError("");
    try {
      if (isV3 && !form.v3_user?.trim()) {
        throw new Error("SNMPv3 user is required");
      }
      await onSave({
        ...form,
        host: form.host.trim(),
        driver_id: form.driver_id || "",
        port: form.port || 161,
        community: form.community || "public",
        v3_user: form.v3_user || "",
        v3_auth_proto: form.v3_auth_proto || "sha",
        v3_auth_key: form.v3_auth_key || "",
        v3_priv_proto: form.v3_priv_proto || "aes128",
        v3_priv_key: form.v3_priv_key || "",
      });
    } catch (e) {
      setSaveError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!onDelete || !initial) return;
    const label = form.name.trim() || form.host.trim() || initial.host;
    if (!window.confirm(`Remove switch “${label}” from inventory?\n\nThis only removes it from swi-mgmt; the device itself is unchanged.`)) {
      return;
    }
    setDeleting(true);
    setSaveError("");
    try {
      await onDelete();
    } catch (e) {
      setSaveError(String(e));
      setDeleting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{initial ? "Edit Switch" : "Add Switch"}</h2>
        <div className="form-row">
          <label>Host</label>
          <input
            value={form.host}
            onChange={(e) => setForm({ ...form, host: e.target.value })}
            autoFocus
          />
        </div>
        <div className="form-row">
          <label>Name</label>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label>SNMP</label>
          <select
            value={form.snmp_version}
            onChange={(e) =>
              setForm({ ...form, snmp_version: Number(e.target.value) })
            }
          >
            <option value={1}>v1</option>
            <option value={2}>v2c</option>
            <option value={3}>v3</option>
          </select>
        </div>
        {!isV3 ? (
          <div className="form-row">
            <label>Community</label>
            <input
              value={form.community}
              onChange={(e) => setForm({ ...form, community: e.target.value })}
              autoComplete="off"
            />
          </div>
        ) : (
          <>
            <div className="form-row">
              <label>User</label>
              <input
                value={form.v3_user || ""}
                onChange={(e) => setForm({ ...form, v3_user: e.target.value })}
                autoComplete="username"
              />
            </div>
            <div className="form-row">
              <label>Auth</label>
              <select
                value={form.v3_auth_proto || "sha"}
                onChange={(e) => setForm({ ...form, v3_auth_proto: e.target.value })}
              >
                <option value="none">none</option>
                <option value="md5">MD5</option>
                <option value="sha">SHA</option>
                <option value="sha224">SHA-224</option>
                <option value="sha256">SHA-256</option>
                <option value="sha384">SHA-384</option>
                <option value="sha512">SHA-512</option>
              </select>
            </div>
            {(form.v3_auth_proto || "sha") !== "none" && (
              <div className="form-row">
                <label>Auth key</label>
                <input
                  type="password"
                  value={form.v3_auth_key || ""}
                  onChange={(e) => setForm({ ...form, v3_auth_key: e.target.value })}
                  autoComplete="new-password"
                />
              </div>
            )}
            <div className="form-row">
              <label>Priv</label>
              <select
                value={form.v3_priv_proto || "aes128"}
                onChange={(e) => setForm({ ...form, v3_priv_proto: e.target.value })}
                disabled={(form.v3_auth_proto || "sha") === "none"}
              >
                <option value="none">none</option>
                <option value="des">DES</option>
                <option value="aes128">AES-128</option>
                <option value="aes192">AES-192</option>
                <option value="aes256">AES-256</option>
              </select>
            </div>
            {(form.v3_priv_proto || "aes128") !== "none" &&
              (form.v3_auth_proto || "sha") !== "none" && (
                <div className="form-row">
                  <label>Priv key</label>
                  <input
                    type="password"
                    value={form.v3_priv_key || ""}
                    onChange={(e) => setForm({ ...form, v3_priv_key: e.target.value })}
                    autoComplete="new-password"
                  />
                </div>
              )}
          </>
        )}
        {saveError && <p className="error-banner">{saveError}</p>}
        <div className="form-actions">
          {onDelete && (
            <button className="danger" onClick={() => void handleDelete()} disabled={busy}>
              {deleting ? "Removing…" : "Remove"}
            </button>
          )}
          <span className="form-actions-spacer" />
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="primary"
            onClick={() => void handleSave()}
            disabled={!form.host.trim() || busy || (isV3 && !form.v3_user?.trim())}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ScanDialog({
  defaults,
  onClose,
  onAdd,
}: {
  defaults: { cidr: string; community: string; snmp_version: number };
  onClose: () => void;
  onAdd: (hosts: ScanResult[], community: string, version: number) => void;
}) {
  const [cidr, setCidr] = useState(defaults.cidr);
  const [community, setCommunity] = useState(defaults.community);
  const [version, setVersion] = useState(defaults.snmp_version);
  const [status, setStatus] = useState({
    running: false,
    phase: "",
    ping_done: 0,
    ping_total: 0,
    snmp_done: 0,
    snmp_total: 0,
    error: "",
    results: [] as ScanResult[],
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    setCidr(defaults.cidr);
    setCommunity(defaults.community);
    setVersion(defaults.snmp_version);
  }, [defaults]);

  const poll = useCallback(async () => {
    const s = await api.getScanStatus();
    setStatus({
      running: s.running,
      phase: s.phase || "",
      ping_done: s.ping_done ?? 0,
      ping_total: s.ping_total ?? 0,
      snmp_done: s.snmp_done ?? 0,
      snmp_total: s.snmp_total ?? 0,
      error: s.error,
      results: s.results,
    });
    return s;
  }, []);

  useEffect(() => {
    if (!status.running) return;
    const id = setInterval(poll, 500);
    return () => clearInterval(id);
  }, [status.running, poll]);

  const start = async () => {
    setSelected(new Set());
    await api.startScan({ cidr, community, snmp_version: version });
    poll();
  };

  const selectAll = () => setSelected(new Set(status.results.map((r) => r.host)));
  const selectNone = () => setSelected(new Set());

  const showProgress = status.running || status.ping_total > 0;
  const pingPct =
    status.ping_total > 0 ? (status.ping_done / status.ping_total) * 100 : 0;
  const snmpPct =
    status.snmp_total > 0 ? (status.snmp_done / status.snmp_total) * 100 : 0;
  const snmpWaiting = status.running && status.phase === "ping";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h2>Scan for Switches</h2>
        <div className="form-row">
          <label>Subnet</label>
          <input value={cidr} onChange={(e) => setCidr(e.target.value)} />
        </div>
        <div className="form-row">
          <label>Community</label>
          <input value={community} onChange={(e) => setCommunity(e.target.value)} />
        </div>
        <div className="form-row">
          <label>SNMP</label>
          <select value={version} onChange={(e) => setVersion(Number(e.target.value))}>
            <option value={1}>v1</option>
            <option value={2}>v2c</option>
          </select>
        </div>
        <div className="scan-actions">
          <button onClick={start} disabled={status.running}>
            {status.running ? "Scanning…" : "Start Scan"}
          </button>
          {status.running && (
            <button onClick={() => api.cancelScan()}>Cancel</button>
          )}
          {status.results.length > 0 && !status.running && (
            <>
              <button onClick={selectAll}>Select all</button>
              <button onClick={selectNone}>Select none</button>
            </>
          )}
        </div>
        {showProgress && (
          <div className="scan-progress">
            <div
              className={`scan-progress-row${
                status.phase === "ping" && status.running ? " active" : ""
              }`}
            >
              <span className="scan-progress-label">Ping</span>
              <div className="progress">
                <div style={{ width: `${pingPct}%` }} />
              </div>
              <span className="scan-progress-count">
                {status.ping_done}/{status.ping_total || "—"}
              </span>
            </div>
            <div
              className={`scan-progress-row${
                status.phase === "snmp" && status.running ? " active" : ""
              }`}
            >
              <span className="scan-progress-label">SNMP</span>
              <div className="progress">
                <div style={{ width: `${snmpWaiting ? 0 : snmpPct}%` }} />
              </div>
              <span className="scan-progress-count">
                {snmpWaiting
                  ? "waiting…"
                  : `${status.snmp_done}/${status.snmp_total}`}
              </span>
            </div>
          </div>
        )}
        <div className="vlan-meta">
          {status.running
            ? status.phase === "snmp"
              ? `Probing SNMP on ${status.snmp_total} pingable host(s)…`
              : `Pinging ${status.ping_total || "…"} address(es)…`
            : status.error ||
              `Found ${status.results.filter((r) => r.snmp_ok !== false).length} SNMP device(s)` +
                (status.results.some((r) => r.snmp_ok === false)
                  ? `, ${status.results.filter((r) => r.snmp_ok === false).length} ping-only`
                  : "")}
        </div>
        <div className="scan-results">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Host</th>
                <th>Name</th>
                <th>Driver</th>
              </tr>
            </thead>
            <tbody>
              {status.results.map((r) => (
                <tr
                  key={r.host}
                  className={`${selected.has(r.host) ? "selected" : ""}${
                    r.snmp_ok === false ? " icmp-only" : ""
                  }`}
                  onClick={() => {
                    const next = new Set(selected);
                    if (next.has(r.host)) next.delete(r.host);
                    else next.add(r.host);
                    setSelected(next);
                  }}
                >
                  <td>
                    <input type="checkbox" checked={selected.has(r.host)} readOnly />
                  </td>
                  <td>{r.host}</td>
                  <td>
                    {r.sys_name ||
                      (r.snmp_ok === false ? "—" : r.sys_descr.slice(0, 40))}
                  </td>
                  <td title={r.sys_descr}>
                    {r.snmp_ok === false ? "Ping only (no SNMP)" : r.driver_name}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="form-actions">
          <button onClick={onClose}>Close</button>
          <button
            className="primary"
            disabled={selected.size === 0}
            onClick={() =>
              onAdd(
                status.results.filter((r) => selected.has(r.host)),
                community,
                version
              )
            }
          >
            Add Selected ({selected.size})
          </button>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [switches, setSwitches] = useState<SwitchConfig[]>([]);
  const [selectedHost, setSelectedHost] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<SwitchSnapshot | null>(null);
  const [snapshotsByHost, setSnapshotsByHost] = useState<Record<string, SwitchSnapshot>>({});
  const [session, setSession] = useState<SessionState>({
    vlans: [],
    highlight_vlan: null,
    pending_conflicts: [],
  });
  const [tab, setTab] = useState<Tab>("panel");
  const [live, setLive] = useState(false);
  const [lastLivePollMs, setLastLivePollMs] = useState<number | null>(null);
  const [loadingHosts, setLoadingHosts] = useState<Set<string>>(() => new Set());
  const [reloadingAll, setReloadingAll] = useState(false);
  const [status, setStatus] = useState("Ready");
  const [showAdd, setShowAdd] = useState(false);
  const [editSwitch, setEditSwitch] = useState<SwitchConfig | undefined>();
  const [showScan, setShowScan] = useState(false);
  const [showScenario, setShowScenario] = useState(false);
  const [switchesPanelCollapsed, setSwitchesPanelCollapsed] = useState(false);
  const [search, setSearch] = useState("");
  const [operFilter, setOperFilter] = useState<OperFilter>("all");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme());
  const [health, setHealth] = useState<Record<string, SwitchHealth>>({});
  const [now, setNow] = useState(Date.now());
  const [appConfig, setAppConfig] = useState<AppConfig | null>(null);
  const [scanDefaults, setScanDefaults] = useState({
    cidr: "",
    community: "public",
    snmp_version: 2,
  });
  const selectedHostRef = useRef<string | null>(null);
  selectedHostRef.current = selectedHost;
  const loadGenRef = useRef<Record<string, number>>({});
  const loadCountRef = useRef<Record<string, number>>({});

  const isLoadingHost = useCallback(
    (host: string | null | undefined) => !!host && loadingHosts.has(host),
    [loadingHosts]
  );

  const beginHostLoad = useCallback((host: string) => {
    loadCountRef.current[host] = (loadCountRef.current[host] ?? 0) + 1;
    setLoadingHosts((prev) => {
      if (prev.has(host)) return prev;
      const next = new Set(prev);
      next.add(host);
      return next;
    });
  }, []);

  const endHostLoad = useCallback((host: string) => {
    const nextCount = Math.max(0, (loadCountRef.current[host] ?? 1) - 1);
    if (nextCount > 0) {
      loadCountRef.current[host] = nextCount;
      return;
    }
    delete loadCountRef.current[host];
    setLoadingHosts((prev) => {
      if (!prev.has(host)) return prev;
      const next = new Set(prev);
      next.delete(host);
      return next;
    });
  }, []);

  const vlanFilter = session.highlight_vlan;
  const fastMode = appConfig?.snmp_fast_mode ?? true;
  const switchOrder: SwitchOrder =
    appConfig?.switch_order === "name" || appConfig?.switch_order === "type"
      ? appConfig.switch_order
      : "ip";

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  const loadSwitches = useCallback(async () => {
    const list = await api.listSwitches();
    setSwitches(list);
  }, []);

  const setSwitchOrder = useCallback(async (order: SwitchOrder) => {
    try {
      const cfg = await api.patchConfig({ switch_order: order });
      setAppConfig(cfg);
      setSwitches(cfg.switches);
    } catch (e) {
      setStatus(`Sort failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);
  const loadSnapshot = useCallback(
    async (host: string, refresh = false, mode?: "full" | "live" | "fast") => {
      const resolvedMode = mode ?? (fastMode ? "fast" : "full");
      // Background live polls must not steal/clear the Loading indicator or
      // flip health to "loading" (races with selection / panel prefetch).
      const trackLoading = resolvedMode !== "live";
      let gen: number;
      if (trackLoading) {
        gen = (loadGenRef.current[host] ?? 0) + 1;
        loadGenRef.current[host] = gen;
        beginHostLoad(host);
        if (selectedHostRef.current === host) {
          setStatus(refresh ? `Querying ${host}…` : `Loading ${host}…`);
        }
        setHealth((prev) => ({
          ...prev,
          [host]: {
            status: "loading",
            lastOkAt: prev[host]?.lastOkAt ?? null,
            lastError: prev[host]?.lastError ?? "",
            upPorts: prev[host]?.upPorts ?? 0,
            totalPorts: prev[host]?.totalPorts ?? 0,
          },
        }));
      } else {
        // Soft poll: remember gen at start; do not bump (avoids invalidating UI loads).
        gen = loadGenRef.current[host] ?? 0;
      }
      try {
        const res = await api.getSnapshot(host, refresh, { mode: resolvedMode });
        if (loadGenRef.current[host] !== gen) return;
        if (!trackLoading && (loadCountRef.current[host] ?? 0) > 0) return;
        setSession(res.session);
        const id = res.snapshot.identity;
        const up = res.snapshot.ports.filter((p) => p.oper_status === "UP").length;
        setHealth((prev) => ({
          ...prev,
          [host]: {
            status: "ok",
            lastOkAt: Date.now(),
            lastError: "",
            upPorts: up,
            totalPorts: res.snapshot.ports.length,
          },
        }));
        setSnapshotsByHost((prev) => ({ ...prev, [host]: res.snapshot }));
        if (selectedHostRef.current === host) {
          setSnapshot(res.snapshot);
          setStatus(
            `${id.sys_name || id.host} | ${id.vendor} ${id.model} | ${res.snapshot.vlans.length} VLANs, ${res.snapshot.ports.length} ports`
          );
        }
      } catch (e) {
        if (loadGenRef.current[host] !== gen) return;
        if (!trackLoading && (loadCountRef.current[host] ?? 0) > 0) return;
        const msg = String(e);
        setHealth((prev) => ({
          ...prev,
          [host]: {
            status: "error",
            lastOkAt: prev[host]?.lastOkAt ?? null,
            lastError: msg,
            upPorts: prev[host]?.upPorts ?? 0,
            totalPorts: prev[host]?.totalPorts ?? 0,
          },
        }));
        if (selectedHostRef.current === host) {
          setStatus("Error — see ! on switch");
        }
      } finally {
        if (trackLoading) endHostLoad(host);
      }
    },
    [fastMode, beginHostLoad, endHostLoad]
  );

  const reloadAll = useCallback(async () => {
    if (switches.length === 0) return;
    setReloadingAll(true);
    setStatus(`Reloading ${switches.length} switches…`);
    const mode = fastMode ? "fast" : "full";
    try {
      for (const sw of switches) {
        await loadSnapshot(sw.host, true, mode);
      }
    } finally {
      setReloadingAll(false);
    }
  }, [switches, loadSnapshot, fastMode]);

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      // Desktop .app may wait on PyInstaller sidecar cold-start (often several seconds).
      const attempts = 60;
      let lastErr: unknown;
      setStatus("Starting local API…");
      for (let i = 0; i < attempts; i++) {
        if (cancelled) return;
        try {
          await loadSwitches();
          const cfg = await api.getConfig();
          if (cancelled) return;
          setAppConfig(cfg);
          try {
            const def = await api.getDefaultSubnet();
            if (cancelled) return;
            setScanDefaults({
              cidr: def.cidr || cfg.scan_subnet || "192.168.1.0/24",
              community: cfg.scan_community || "public",
              snmp_version: cfg.scan_version || 2,
            });
          } catch {
            /* optional */
          }
          setStatus("Ready");
          return;
        } catch (e) {
          lastErr = e;
          if (i === 8) setStatus("Waiting for local API…");
          await new Promise((r) => setTimeout(r, 250));
        }
      }
      if (!cancelled) {
        const detail = lastErr instanceof Error ? lastErr.message : String(lastErr);
        setStatus(
          `Config error: ${detail}. Local API did not become ready — quit other SWI-MGMT ` +
            `instances, and if macOS asked for Documents access while the .app lives under ` +
            `Documents/, Allow it or move the app to /Applications.`
        );
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadSwitches]);

  useEffect(() => {
    if (selectedHost) loadSnapshot(selectedHost);
    else setSnapshot(null);
  }, [selectedHost, loadSnapshot]);

  useEffect(() => {
    if (!live) {
      setLastLivePollMs(null);
      return;
    }
    const pauseMs = Math.max(0.5, appConfig?.poll_interval_sec ?? 30) * 1000;
    const pollAll = tab === "panel";
    if (pollAll && switches.length === 0) return;
    if (!pollAll && !selectedHost) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const scheduleNext = () => {
      if (cancelled) return;
      timer = setTimeout(() => void tick(), pauseMs);
    };

    const tick = async () => {
      if (cancelled) return;
      const started = performance.now();
      try {
        if (pollAll) {
          for (const sw of switches) {
            if (cancelled) return;
            await loadSnapshot(sw.host, true, "live");
          }
        } else if (selectedHost) {
          await loadSnapshot(selectedHost, true, "live");
        }
      } finally {
        if (!cancelled) {
          setLastLivePollMs(performance.now() - started);
        }
        scheduleNext();
      }
    };

    // Run immediately, then pause poll_interval_sec after each cycle finishes.
    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [live, selectedHost, loadSnapshot, appConfig, tab, switches]);

  useEffect(() => {
    if (tab !== "panel") return;
    let cancelled = false;
    const run = async () => {
      for (const sw of switches) {
        if (cancelled) return;
        if ((loadCountRef.current[sw.host] ?? 0) > 0) continue;
        const h = health[sw.host];
        if (h?.status === "loading") continue;
        if (snapshotsByHost[sw.host] && h?.status === "ok") continue;
        await loadSnapshot(sw.host, false, fastMode ? "fast" : "full");
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
    // Intentionally depend on switch list + tab; health/snapshots checked inside.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, switches, loadSnapshot, fastMode]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const onVlanClick = async (vlanId: number) => {
    const next = vlanFilter === vlanId ? null : vlanId;
    const s = await api.setHighlight(next);
    setSession(s);
  };

  const clearVlanFilter = async () => {
    const s = await api.setHighlight(null);
    setSession(s);
  };

  const onResolve = async (vlanId: number, choice: "session" | "switch") => {
    const s = await api.resolveConflict(vlanId, choice);
    setSession(s);
  };

  const onResolveAll = async (choice: "session" | "switch") => {
    const s = await api.resolveAllConflicts(choice);
    setSession(s);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === "1") setTab("panel");
      if (e.key === "2") setTab("ports");
      if (e.key === "3") setTab("vlans");
      if (e.key === "4") setTab("matrix");
      if (e.key === "5") setTab("live");
      if ((e.key === "r" || e.key === "R") && selectedHost && !isLoadingHost(selectedHost)) {
        loadSnapshot(selectedHost, true);
      }
      if (e.key === "Escape" && session.highlight_vlan != null) {
        api.setHighlight(null).then(setSession);
      }
      if (
        (e.key === "l" || e.key === "L") &&
        (tab === "panel" ? switches.length > 0 : !!selectedHost)
      ) {
        setLive((v) => !v);
      }
      if (e.key === "f" || e.key === "F") {
        const next = !(appConfig?.snmp_fast_mode ?? true);
        api.patchConfig({ snmp_fast_mode: next }).then(setAppConfig);
      }
      if (e.key === "t" || e.key === "T") {
        setTheme((t) => (t === "dark" ? "light" : "dark"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedHost, isLoadingHost, loadSnapshot, session.highlight_vlan, appConfig, tab, switches.length]);

  const filteredPorts = useMemo(() => {
    if (!snapshot) return [];
    return filterPorts(snapshot.ports, {
      vlanFilter,
      operFilter,
      roleFilter,
      search,
    });
  }, [snapshot, vlanFilter, operFilter, roleFilter, search]);

  const highlightVlan = session.vlans.find((v) => v.vlan_id === vlanFilter);
  const liveAvailable = tab === "panel" ? switches.length > 0 : !!selectedHost;
  const pollIntervalSec = appConfig?.poll_interval_sec ?? 30;

  return (
    <div className={`app${switchesPanelCollapsed ? " switches-collapsed" : ""}`}>
      <div className="toolbar">
        <div className="toolbar-right" role="group" aria-label="Display options">
          <div className="toolbar-live">
            <button
              type="button"
              className={live ? "primary" : ""}
              onClick={() => setLive(!live)}
              disabled={!liveAvailable}
              title={
                tab === "panel"
                  ? "Toggle live polling of all switches (L)"
                  : "Toggle live polling of the selected switch (L)"
              }
            >
              Live {live ? "On" : "Off"}
            </button>
            <LiveIntervalControl
              live={live}
              valueSec={pollIntervalSec}
              lastPollMs={lastLivePollMs}
              title={
                tab === "panel"
                  ? `Pause ${formatPollInterval(pollIntervalSec)} after each full live poll of all switches`
                  : `Pause ${formatPollInterval(pollIntervalSec)} after each live poll of the selected switch`
              }
              onChange={async (sec) => {
                const cfg = await api.patchConfig({ poll_interval_sec: sec });
                setAppConfig(cfg);
              }}
            />
          </div>
          <button
            type="button"
            className={fastMode ? "toolbar-fast primary" : "toolbar-fast"}
            onClick={async () => {
              const next = !fastMode;
              const cfg = await api.patchConfig({ snmp_fast_mode: next });
              setAppConfig(cfg);
            }}
            title="Fast mode skips traffic counters (In/Out rates hidden) (F)"
          >
            Fast {fastMode ? "On" : "Off"}
          </button>
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            title="Toggle light/dark mode (T)"
          >
            <span className="theme-icon" aria-hidden>
              {theme === "dark" ? "☀" : "☾"}
            </span>
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </div>

      <div className="main">
        <div
          className={`panel switches-panel${switchesPanelCollapsed ? " collapsed" : ""}`}
        >
          <div className="panel-title">
            {!switchesPanelCollapsed && <span>Switches</span>}
            <div className="panel-title-actions">
              {!switchesPanelCollapsed && (
                <>
                  <div className="switch-sort" role="group" aria-label="Sort switches by">
                    {(
                      [
                        ["ip", "IP"],
                        ["name", "Name"],
                        ["type", "Type"],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        className={`switch-sort-btn${switchOrder === value ? " active" : ""}`}
                        aria-pressed={switchOrder === value}
                        title={`Sort by ${label}`}
                        onClick={() => void setSwitchOrder(value)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="icon-btn panel-title-btn"
                    title="Reload all switches"
                    aria-label="Reload all switches"
                    disabled={reloadingAll || switches.length === 0}
                    onClick={() => void reloadAll()}
                  >
                    ↻
                  </button>
                </>
              )}
              <button
                type="button"
                className="icon-btn panel-title-btn"
                onClick={() => setSwitchesPanelCollapsed((v) => !v)}
                title={
                  switchesPanelCollapsed ? "Show switches column" : "Collapse switches column"
                }
                aria-label={
                  switchesPanelCollapsed ? "Show switches column" : "Collapse switches column"
                }
                aria-expanded={!switchesPanelCollapsed}
              >
                {switchesPanelCollapsed ? "»" : "«"}
              </button>
            </div>
          </div>
          {switchesPanelCollapsed ? (
            <button
              type="button"
              className="switches-rail"
              onClick={() => setSwitchesPanelCollapsed(false)}
              title="Show switches column"
            >
              <span className="switches-rail-label">Switches</span>
            </button>
          ) : (
            <>
              <div className="panel-body">
                {switches.length === 0 ? (
                  <div className="empty-state">
                    <p>No switches yet.</p>
                    <p className="vlan-meta">Use Add or Scan below to get started.</p>
                  </div>
                ) : (
                  switches.map((sw) => {
                    const h = health[sw.host];
                    const snmpLabel =
                      sw.snmp_version === 1 ? "v1" : sw.snmp_version === 3 ? "v3" : "v2c";
                    const isLoading = isLoadingHost(sw.host) || h?.status === "loading";
                    const lastError = h?.lastError || "";
                    const selfId = (sw.name || "").trim();
                    const dnsName = (sw.dns_name || "").trim();
                    const showDns =
                      !!dnsName && dnsName.toLowerCase() !== sw.host.toLowerCase();
                    return (
                      <div
                        key={sw.host}
                        className={`switch-item ${selectedHost === sw.host ? "active" : ""}`}
                        onClick={() => setSelectedHost(sw.host)}
                      >
                        <div className="switch-row">
                          <span
                            className={`health-dot ${h?.status || "unknown"}`}
                            title={h?.lastError || h?.status || "unknown"}
                          />
                          <div className="switch-info">
                            <div
                              className={`switch-name${selfId ? "" : " is-empty"}`}
                              title={
                                selfId
                                  ? "Device / configured name"
                                  : "No self-identification name"
                              }
                            >
                              {selfId || "—"}
                            </div>
                            <div className="switch-meta-row">
                              <div className="switch-meta">
                                <span className="switch-host" title="IP address">
                                  {sw.host}
                                </span>
                                {showDns ? (
                                  <>
                                    <span className="meta-sep" aria-hidden>
                                      ·
                                    </span>
                                    <span className="switch-dns" title="Reverse DNS (PTR)">
                                      {dnsName}
                                    </span>
                                  </>
                                ) : null}
                                <span className="meta-sep" aria-hidden>
                                  ·
                                </span>
                                <span className="snmp-compact" title={`SNMP ${snmpLabel}`}>
                                  {snmpLabel}
                                </span>
                                <span className="meta-sep" aria-hidden>
                                  ·
                                </span>
                                <span className="switch-health-text">
                                  {h?.status === "ok" &&
                                    `${h.upPorts}/${h.totalPorts} up · ${formatAge(h.lastOkAt, now)}`}
                                  {h?.status === "error" &&
                                    `Error · ${formatAge(h.lastOkAt, now)}`}
                                  {h?.status === "loading" && "Loading…"}
                                  {!h && "Not loaded"}
                                </span>
                              </div>
                              <div className="switch-actions-inline">
                                <div className="switch-error-slot">
                                  {lastError ? (
                                    <SwitchErrorButton message={lastError} />
                                  ) : null}
                                </div>
                                <button
                                  type="button"
                                  className="icon-btn switch-reload"
                                  title="Refresh this switch"
                                  aria-label={`Refresh ${selfId || sw.host}`}
                                  disabled={isLoading}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    void loadSnapshot(sw.host, true, "full");
                                  }}
                                >
                                  ↻
                                </button>
                                <button
                                  type="button"
                                  className="icon-btn switch-gear"
                                  title="Edit switch"
                                  aria-label={`Edit ${selfId || sw.host}`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setEditSwitch(sw);
                                  }}
                                >
                                  ⚙
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
              <div className="panel-footer switch-actions">
                <button onClick={() => setShowAdd(true)}>Add</button>
                <button className="primary" onClick={() => setShowScan(true)}>
                  Scan
                </button>
                <button
                  type="button"
                  title="Export or import switch list and SNMP settings"
                  onClick={() => setShowScenario(true)}
                >
                  Scenario
                </button>
              </div>
            </>
          )}
        </div>

        <VlanList session={session} onSelect={onVlanClick} />

        <div className="content">
          <div className="tabs">
            {(
              [
                ["panel", "Front Panel", "1"],
                ["ports", "Ports", "2"],
                ["vlans", "VLAN List", "3"],
                ["matrix", "VLAN Matrix", "4"],
                ["live", "Live View", "5"],
              ] as const
            ).map(([t, label, key]) => (
              <button
                key={t}
                type="button"
                className={`tab ${tab === t ? "active" : ""}`}
                onClick={() => setTab(t)}
                title={`Shortcut ${key}`}
              >
                {label}
              </button>
            ))}
            <div className="tab-spacer" />
          </div>

          <div className="content-bar" aria-live="polite">
            {vlanFilter != null && highlightVlan && (
              <button type="button" className="chip" onClick={clearVlanFilter}>
                <span
                  className="chip-swatch"
                  style={{ background: highlightVlan.color }}
                />
                VLAN {highlightVlan.vlan_id} {highlightVlan.name || ""}
                <span className="chip-x">×</span>
              </button>
            )}
            {(tab === "ports" || tab === "matrix" || tab === "live") && (
              <PortFilters
                search={search}
                operFilter={operFilter}
                roleFilter={roleFilter}
                onSearch={setSearch}
                onOper={setOperFilter}
                onRole={setRoleFilter}
              />
            )}
            {(tab === "ports" || tab === "matrix" || tab === "live") && snapshot && (
              <span className="vlan-meta">
                {filteredPorts.length}/{snapshot.ports.length} ports
              </span>
            )}
            <span className="content-bar-spacer" />
            {isLoadingHost(selectedHost) && (
              <span className="loading-inline">Loading</span>
            )}
          </div>

          <div className="tab-content">
            {tab === "panel" ? (
              <FrontPanelView
                switches={switches}
                snapshotsByHost={snapshotsByHost}
                healthByHost={health}
                session={session}
                vlanFilter={vlanFilter}
                selectedHost={selectedHost}
                onSelectHost={setSelectedHost}
              />
            ) : !snapshot && !isLoadingHost(selectedHost) ? (
              <div className="empty-state large">
                {selectedHost ? (
                  <p>Could not load this switch. Open ! on its list entry for details.</p>
                ) : (
                  <>
                    <p>Select a switch to begin.</p>
                    {switches.length === 0 && (
                      <>
                        <button className="primary" onClick={() => setShowScan(true)}>
                          Scan network
                        </button>
                        <button onClick={() => setShowAdd(true)}>Add switch</button>
                      </>
                    )}
                  </>
                )}
              </div>
            ) : snapshot && tab === "ports" ? (
              <PortTable
                ports={filteredPorts}
                session={session}
                showMatrix={false}
                showRates={!fastMode}
              />
            ) : snapshot && tab === "vlans" ? (
              <VlanTable
                snapshot={snapshot}
                session={session}
                vlanFilter={vlanFilter}
              />
            ) : snapshot && tab === "matrix" ? (
              <PortTable
                ports={filteredPorts}
                session={session}
                showMatrix={true}
                showRates={false}
              />
            ) : snapshot ? (
              <LiveView ports={filteredPorts} session={session} showRates={!fastMode} />
            ) : null}
          </div>
        </div>
      </div>

      <div className="statusbar">
        <span>{status}</span>
        {selectedHost && health[selectedHost]?.lastOkAt && (
          <span className="status-meta">
            Last success {formatAge(health[selectedHost].lastOkAt, now)}
          </span>
        )}
        <span className="status-meta keys">1 front panel · 2–5 tabs · R refresh · L live · F fast · T theme · Esc clear VLAN</span>
      </div>

      {session.pending_conflicts.length > 0 && (
        <ConflictModal
          conflicts={session.pending_conflicts}
          onResolve={onResolve}
          onResolveAll={onResolveAll}
        />
      )}

      {showAdd && (
        <SwitchDialog
          onClose={() => setShowAdd(false)}
          onSave={async (cfg) => {
            await api.addSwitch(cfg);
            await loadSwitches();
            setSelectedHost(cfg.host);
            setShowAdd(false);
          }}
        />
      )}

      {editSwitch && (
        <SwitchDialog
          initial={editSwitch}
          onClose={() => setEditSwitch(undefined)}
          onSave={async (cfg) => {
            await api.updateSwitch(editSwitch.host, cfg);
            await loadSwitches();
            setEditSwitch(undefined);
            setSelectedHost(cfg.host);
            await loadSnapshot(cfg.host, true, "full");
          }}
          onDelete={async () => {
            const host = editSwitch.host;
            await api.deleteSwitch(host);
            setSnapshotsByHost((prev) => {
              const next = { ...prev };
              delete next[host];
              return next;
            });
            if (selectedHost === host) {
              setSelectedHost(null);
              setSnapshot(null);
            }
            setEditSwitch(undefined);
            await loadSwitches();
          }}
        />
      )}

      {showScan && (
        <ScanDialog
          defaults={scanDefaults}
          onClose={() => setShowScan(false)}
          onAdd={async (results, community, version) => {
            const existing = new Set(switches.map((s) => s.host));
            for (const r of results) {
              if (!existing.has(r.host)) {
                await api.addSwitch({
                  host: r.host,
                  name: r.sys_name,
                  community,
                  snmp_version: version,
                  driver_id: r.driver_id,
                  port: 161,
                });
              }
            }
            try {
              await api.patchConfig({
                scan_community: community,
                scan_version: version,
                scan_subnet: scanDefaults.cidr,
              });
            } catch {
              /* best-effort persist */
            }
            setScanDefaults((d) => ({ ...d, community, snmp_version: version }));
            await loadSwitches();
            setShowScan(false);
          }}
        />
      )}

      {showScenario && (
        <ScenarioDialog
          currentSwitchCount={switches.length}
          onClose={() => setShowScenario(false)}
          onImported={(cfg, message) => {
            setAppConfig(cfg);
            setSwitches(cfg.switches);
            setScanDefaults((d) => ({
              ...d,
              community: cfg.scan_community || d.community,
              snmp_version: cfg.scan_version || d.snmp_version,
              cidr: cfg.scan_subnet || d.cidr,
            }));
            setSnapshotsByHost({});
            setSnapshot(null);
            setHealth({});
            const hosts = new Set(cfg.switches.map((s) => s.host));
            if (selectedHost && !hosts.has(selectedHost)) {
              setSelectedHost(null);
            }
            setStatus(message);
            setShowScenario(false);
          }}
        />
      )}
    </div>
  );
}

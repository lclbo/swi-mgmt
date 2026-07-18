import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";
import type { PortStatus, SessionState, SwitchConfig, SwitchSnapshot } from "./types";
import {
  placeholderLayout,
  portsByPanel,
  resolveSwitchLayout,
  type LayoutSlot,
  type PortMedia,
  type SwitchLayout,
} from "./switchLayouts";

/** Don't shrink jacks below this; section scrolls instead. */
const MIN_FP_SCALE = 0.55;

function vlanColor(session: SessionState, vlanId: number): string {
  return session.vlans.find((v) => v.vlan_id === vlanId)?.color || "#64748b";
}

function vlanName(session: SessionState, vlanId: number): string {
  return session.vlans.find((v) => v.vlan_id === vlanId)?.name || "";
}

function portUntagged(port: PortStatus): number[] {
  if (port.untagged_vlans?.length) return port.untagged_vlans;
  if (port.tagged_vlans.includes(port.primary_vlan)) return [];
  return [port.primary_vlan];
}

function portHasVlan(port: PortStatus, vlanId: number): boolean {
  return (
    portUntagged(port).includes(vlanId) ||
    port.tagged_vlans.includes(vlanId) ||
    port.primary_vlan === vlanId
  );
}

function isMultiVlan(port: PortStatus): boolean {
  const ids = new Set([...portUntagged(port), ...port.tagged_vlans]);
  return ids.size > 1 || port.tagged_vlans.length > 0;
}

function linkClass(port: PortStatus | undefined): "up" | "down" | "admin-down" | "empty" {
  if (!port) return "empty";
  if (port.admin_status === "DOWN") return "admin-down";
  if (port.oper_status === "UP") return "up";
  return "down";
}

function formatPoeDetection(detection: string | null | undefined): string {
  switch (detection) {
    case "delivering":
      return "Delivering power";
    case "searching":
      return "Searching";
    case "disabled":
      return "Disabled";
    case "fault":
      return "Fault";
    case "test":
      return "Test";
    case "otherFault":
      return "Other fault";
    default:
      return detection || "Unknown";
  }
}

function formatPoePower(mw: number | null | undefined): string | null {
  if (mw == null || Number.isNaN(mw)) return null;
  if (mw >= 1000) return `${(mw / 1000).toFixed(1)} W`;
  return `${mw} mW`;
}

function PoeBolt() {
  return (
    <span className="fp-poe-bolt" title="PoE delivering power" aria-label="PoE delivering power">
      <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden>
        <path
          fill="currentColor"
          d="M9.2 1.1 3.6 8.4h3.1l-.9 6.5 5.8-8.1H8.4L9.2 1.1z"
        />
      </svg>
    </span>
  );
}

/** Place popover beside the jack, clamped to the viewport. */
function placeBesideAnchor(
  anchor: DOMRect,
  popW: number,
  popH: number
): { x: number; y: number } {
  const gap = 10;
  const margin = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const preferRight = anchor.right + gap + popW <= vw - margin;
  let x = preferRight ? anchor.right + gap : anchor.left - gap - popW;
  x = Math.min(Math.max(margin, x), Math.max(margin, vw - margin - popW));

  // Center on the jack, then clamp so the card stays on-screen (and near the port).
  let y = anchor.top + (anchor.height - popH) / 2;
  y = Math.min(Math.max(margin, y), Math.max(margin, vh - margin - popH));

  return { x, y };
}

function JackFace({
  media,
  link,
  mediaMode,
}: {
  media: PortMedia;
  link: ReturnType<typeof linkClass>;
  mediaMode?: string | null;
}) {
  if (media === "sfp" || media === "sfp+") {
    return (
      <div className={`jack-face sfp ${link}`} data-media={media}>
        <div className="sfp-cage">
          <div className="sfp-module">
            <span className="sfp-eye" />
            <span className="sfp-eye" />
          </div>
        </div>
      </div>
    );
  }
  if (media === "combo") {
    // Only split green when link is up and active media is known.
    const mode =
      link === "up" && (mediaMode === "copper" || mediaMode === "fiber")
        ? mediaMode
        : null;
    const title = mode
      ? `Combo · up · active ${mode === "copper" ? "RJ45 (copper)" : "SFP (fiber)"}`
      : `Combo (RJ45 / SFP) · link ${
          link === "up" ? "up" : link === "admin-down" ? "admin down" : link === "down" ? "down" : "—"
        }`;
    return (
      <div
        className={`jack-face combo ${link}${mode ? ` combo-mode-${mode}` : ""}`}
        title={title}
        data-media-mode={mode || undefined}
      >
        <div className="combo-half combo-copper" aria-hidden>
          <div className="rj45-body">
            <div className="rj45-opening">
              <div className="rj45-contacts" />
            </div>
            <span className="rj45-tab" />
          </div>
        </div>
        <div className="combo-half combo-fiber" aria-hidden>
          <div className="sfp-cage">
            <div className="sfp-module">
              <span className="sfp-eye" />
              <span className="sfp-eye" />
            </div>
          </div>
        </div>
        <span className="combo-diag" aria-hidden />
      </div>
    );
  }
  return (
    <div className={`jack-face rj45 ${link}`}>
      <div className="rj45-body">
        <div className="rj45-opening">
          <div className="rj45-contacts" />
        </div>
        <span className="rj45-tab" />
      </div>
    </div>
  );
}

interface HoverInfo {
  host: string;
  slot: LayoutSlot;
  port: PortStatus;
  anchor: DOMRect;
}

function PortHoverCard({
  info,
  session,
}: {
  info: HoverInfo;
  session: SessionState;
}) {
  const { port, slot, anchor } = info;
  const untagged = portUntagged(port);
  const tagged = port.tagged_vlans.filter((v) => !untagged.includes(v));
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(() =>
    placeBesideAnchor(anchor, 300, 180)
  );

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setPos(placeBesideAnchor(anchor, width, height));
  }, [
    anchor.top,
    anchor.left,
    anchor.right,
    anchor.bottom,
    anchor.width,
    anchor.height,
    port.index,
    slot.panel,
    untagged.length,
    tagged.length,
    port.poe?.delivering,
    port.poe?.detection,
    port.poe?.power_mw,
  ]);

  const poe = port.poe;
  const poePower = poe ? formatPoePower(poe.power_mw) : null;

  return (
    <div
      ref={ref}
      className="fp-port-popover"
      style={{ left: pos.x, top: pos.y }}
      role="tooltip"
    >
      <div className="fp-pop-head">
        <span className="fp-pop-port">Port {slot.panel}</span>
        <span className="fp-pop-media">
          {slot.media === "combo"
            ? port.media_mode === "copper"
              ? "combo · copper"
              : port.media_mode === "fiber"
                ? "combo · fiber"
                : "combo"
            : slot.media}
        </span>
      </div>
      <div className="fp-pop-row">
        <span className={`fp-pop-pill ${linkClass(port)}`}>
          {port.admin_status === "DOWN"
            ? "Admin down"
            : port.oper_status === "UP"
              ? "Link up"
              : "Link down"}
        </span>
        {port.speed_mbps != null && (
          <span className="fp-pop-meta">{port.speed_mbps} Mb/s</span>
        )}
      </div>
      {poe && (
        <div className="fp-pop-section">
          <div className="fp-pop-section-title">PoE</div>
          <div className="fp-pop-kv">
            {poe.admin_enable != null && (
              <div className="fp-pop-kv-row">
                <span className="fp-pop-kv-key">Admin</span>
                <span className="fp-pop-kv-val">
                  {poe.admin_enable ? "Enabled" : "Disabled"}
                </span>
              </div>
            )}
            {poe.detection != null && (
              <div className="fp-pop-kv-row">
                <span className="fp-pop-kv-key">Status</span>
                <span
                  className={`fp-pop-kv-val${poe.delivering ? " poe-active" : ""}`}
                >
                  {formatPoeDetection(poe.detection)}
                  {poe.delivering ? " · active" : ""}
                </span>
              </div>
            )}
            {poePower && (
              <div className="fp-pop-kv-row">
                <span className="fp-pop-kv-key">Power</span>
                <span className="fp-pop-kv-val">{poePower}</span>
              </div>
            )}
            {poe.power_class != null && (
              <div className="fp-pop-kv-row">
                <span className="fp-pop-kv-key">Class</span>
                <span className="fp-pop-kv-val">{poe.power_class}</span>
              </div>
            )}
            {poe.priority && (
              <div className="fp-pop-kv-row">
                <span className="fp-pop-kv-key">Priority</span>
                <span className="fp-pop-kv-val">
                  {poe.priority.charAt(0).toUpperCase() + poe.priority.slice(1)}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
      <div className="fp-pop-section">
        <div className="fp-pop-section-title">Untagged</div>
        {untagged.length === 0 ? (
          <div className="fp-pop-empty">None</div>
        ) : (
          <div className="fp-pop-vlans">
            {untagged.map((vid) => (
              <span
                key={`u-${vid}`}
                className="fp-vlan-chip untagged"
                style={{ background: vlanColor(session, vid) }}
              >
                <span className="fp-vlan-id">{vid}</span>
                {vlanName(session, vid) && (
                  <span className="fp-vlan-name">{vlanName(session, vid)}</span>
                )}
                <span className="fp-vlan-tag">U</span>
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="fp-pop-section">
        <div className="fp-pop-section-title">Tagged</div>
        {tagged.length === 0 ? (
          <div className="fp-pop-empty">None</div>
        ) : (
          <div className="fp-pop-vlans">
            {tagged.map((vid) => (
              <span
                key={`t-${vid}`}
                className="fp-vlan-chip tagged"
                style={{
                  borderColor: vlanColor(session, vid),
                  background: `color-mix(in srgb, ${vlanColor(session, vid)} 28%, transparent)`,
                }}
              >
                <span
                  className="fp-vlan-swatch"
                  style={{ background: vlanColor(session, vid) }}
                />
                <span className="fp-vlan-id">{vid}</span>
                {vlanName(session, vid) && (
                  <span className="fp-vlan-name">{vlanName(session, vid)}</span>
                )}
                <span className="fp-vlan-tag">T</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PortJack({
  host,
  slot,
  port,
  session,
  dimmed,
  placeholder,
  onHover,
  onLeave,
}: {
  host: string;
  slot: LayoutSlot;
  port: PortStatus | undefined;
  session: SessionState;
  dimmed: boolean;
  placeholder: boolean;
  onHover: (info: HoverInfo) => void;
  onLeave: () => void;
}) {
  const link = placeholder ? "empty" : linkClass(port);
  const primaryVid = port ? port.primary_vlan || portUntagged(port)[0] || 1 : 1;
  const color = port && !placeholder ? vlanColor(session, primaryVid) : "transparent";
  const multi = port && !placeholder ? isMultiVlan(port) : false;
  const access = port && !placeholder ? !multi : false;

  const style: CSSProperties | undefined =
    port && !placeholder ? ({ ["--fp-vlan"]: color } as CSSProperties) : undefined;

  const report = (e: MouseEvent<HTMLDivElement>) => {
    if (!port || placeholder) return;
    const anchor = e.currentTarget.getBoundingClientRect();
    onHover({ host, slot, port, anchor });
  };

  return (
    <div
      className={[
        "fp-jack",
        access ? "access" : "",
        multi ? "multi" : "",
        dimmed ? "dimmed" : "",
        !port || placeholder ? "missing" : "",
        placeholder ? "placeholder-jack" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={style}
      onMouseEnter={report}
      onMouseMove={report}
      onMouseLeave={onLeave}
    >
      <div className="fp-jack-face">
        <JackFace media={slot.media} link={link} mediaMode={port?.media_mode} />
        {port?.poe?.delivering && !placeholder ? <PoeBolt /> : null}
      </div>
      <div className="fp-label">{slot.panel}</div>
    </div>
  );
}

function ChassisBody({
  layout,
  byPanel,
  session,
  vlanFilter,
  host,
  placeholder,
  onHover,
  onLeave,
}: {
  layout: SwitchLayout;
  byPanel: Map<number, PortStatus>;
  session: SessionState;
  vlanFilter: number | null;
  host: string;
  placeholder: boolean;
  onHover: (info: HoverInfo) => void;
  onLeave: () => void;
}) {
  const copper = layout.slots.filter((s) => s.section === "copper");
  const fiber = layout.slots.filter((s) => s.section === "fiber");

  const renderSection = (slots: LayoutSlot[], cols: number, kind: string) => {
    if (!slots.length) return null;
    return (
      <div
        className={`fp-section fp-${kind}`}
        style={{ ["--fp-cols" as string]: Math.max(cols, 1) }}
      >
        {slots.map((slot) => {
          const port = byPanel.get(slot.panel);
          const dimmed =
            !placeholder &&
            vlanFilter != null &&
            (!port || !portHasVlan(port, vlanFilter));
          return (
            <div
              key={`${slot.section}-${slot.panel}-${slot.row}-${slot.col}`}
              className="fp-cell"
              style={{
                gridColumn: slot.col + 1,
                gridRow: slot.row + 1,
              }}
            >
              <PortJack
                host={host}
                slot={slot}
                port={port}
                session={session}
                dimmed={dimmed}
                placeholder={placeholder}
                onHover={onHover}
                onLeave={onLeave}
              />
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className={`front-panel-chassis${placeholder ? " offline" : ""}`}>
      {renderSection(copper, layout.copperCols, "copper")}
      {fiber.length > 0 && <div className="fp-divider" aria-hidden />}
      {renderSection(fiber, layout.fiberCols, "fiber")}
    </div>
  );
}

/** Apply uniform scale while reserving the scaled layout box. */
function ChassisScaleWrap({ scale, children }: { scale: number; children: ReactNode }) {
  const innerRef = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState({ w: 0, h: 0 });

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    const update = () => {
      setNatural({ w: el.offsetWidth, h: el.offsetHeight });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      className="front-panel-chassis-wrap"
      style={
        natural.w > 0
          ? {
              width: natural.w * scale,
              height: natural.h * scale,
            }
          : undefined
      }
    >
      <div
        ref={innerRef}
        className="front-panel-chassis-scale"
        style={{
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      >
        {children}
      </div>
    </div>
  );
}

export function FrontPanelView({
  switches,
  snapshotsByHost,
  healthByHost,
  session,
  vlanFilter,
  selectedHost,
  onSelectHost,
}: {
  switches: SwitchConfig[];
  snapshotsByHost: Record<string, SwitchSnapshot>;
  healthByHost: Record<
    string,
    { status: string; totalPorts: number; upPorts?: number; lastError?: string } | undefined
  >;
  session: SessionState;
  vlanFilter: number | null;
  selectedHost: string | null;
  onSelectHost: (host: string) => void;
}) {
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [scale, setScale] = useState(1);
  const listRef = useRef<HTMLDivElement>(null);

  const refSize = useMemo(() => {
    for (const sw of switches) {
      const snap = snapshotsByHost[sw.host];
      if (snap?.ports.length) return snap.ports.length;
    }
    for (const sw of switches) {
      const n = healthByHost[sw.host]?.totalPorts;
      if (n) return n;
    }
    return 28;
  }, [switches, snapshotsByHost, healthByHost]);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list) return;

    const measure = () => {
      const chassis = list.querySelectorAll<HTMLElement>(".front-panel-chassis");
      if (!chassis.length) {
        setScale(1);
        return;
      }

      let maxW = 0;
      for (const el of chassis) {
        maxW = Math.max(maxW, el.offsetWidth);
      }
      if (maxW <= 0) return;

      /* Use list viewport width, not block width (blocks may grow with content). */
      const listPad =
        (parseFloat(getComputedStyle(list).paddingLeft) || 0) +
        (parseFloat(getComputedStyle(list).paddingRight) || 0);
      const blockPad = 24; /* .fp-switch-block horizontal padding */
      const avail = list.clientWidth - listPad - blockPad;
      if (avail <= 0) return;

      const fit = avail / maxW;
      setScale(Math.min(1, Math.max(MIN_FP_SCALE, fit)));
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(list);
    for (const el of list.querySelectorAll(".front-panel-chassis")) {
      ro.observe(el);
    }
    return () => ro.disconnect();
  }, [switches, snapshotsByHost, healthByHost, refSize]);

  return (
    <div className="front-panel-wrap multi">
      <div className="front-panel-meta sticky">
        <span className="fp-chassis-label">All switches</span>
        <span className="vlan-meta">{switches.length} in inventory</span>
        <span className="fp-legend">
          <span className="fp-leg link-up" /> up
          <span className="fp-leg link-down" /> down
          <span className="fp-leg link-admin" /> admin down
          <span className="fp-leg vlan-access" /> access
          <span className="fp-leg vlan-multi" /> trunk / multi
        </span>
      </div>

      <div className="front-panel-list" ref={listRef}>
        {switches.length === 0 ? (
          <div className="empty-hint">No switches in inventory.</div>
        ) : (
          switches.map((sw) => {
            const snap = snapshotsByHost[sw.host];
            const health = healthByHost[sw.host];
            const online = !!snap && health?.status === "ok";
            const loading = health?.status === "loading";

            const byPanel = snap ? portsByPanel(snap.ports) : new Map<number, PortStatus>();

            const layout = snap
              ? resolveSwitchLayout(
                  snap.identity.model || snap.identity.sys_descr || "",
                  snap.identity.driver_id || "",
                  [...byPanel.keys()]
                )
              : placeholderLayout(health?.totalPorts || refSize);

            const placeholder = !snap;

            return (
              <div
                key={sw.host}
                className={`fp-switch-block${selectedHost === sw.host ? " selected" : ""}${
                  placeholder ? " is-placeholder" : ""
                }`}
                onClick={() => onSelectHost(sw.host)}
              >
                <div className="fp-switch-head">
                  <span className="fp-switch-name">{(sw.name || "").trim() || "—"}</span>
                  <span className="vlan-meta" title="IP address">
                    {sw.host}
                  </span>
                  {(sw.dns_name || "").trim() &&
                  (sw.dns_name || "").trim().toLowerCase() !== sw.host.toLowerCase() ? (
                    <span className="vlan-meta" title="Reverse DNS (PTR)">
                      {(sw.dns_name || "").trim()}
                    </span>
                  ) : null}
                  {snap && (
                    <span className="vlan-meta">
                      {snap.identity.model || layout.label}
                    </span>
                  )}
                  {loading && <span className="fp-status loading">Loading…</span>}
                  {placeholder && !loading && (
                    <span className="fp-status offline">
                      {health?.status === "error" ? "Offline" : "Not loaded"}
                    </span>
                  )}
                  {online && health && (
                    <span className="fp-status ok">
                      {health.upPorts ?? 0}/{health.totalPorts ?? snap.ports.length} up
                    </span>
                  )}
                </div>
                <ChassisScaleWrap scale={scale}>
                  <ChassisBody
                    layout={layout}
                    byPanel={byPanel}
                    session={session}
                    vlanFilter={vlanFilter}
                    host={sw.host}
                    placeholder={placeholder}
                    onHover={setHover}
                    onLeave={() => setHover(null)}
                  />
                </ChassisScaleWrap>
              </div>
            );
          })
        )}
      </div>

      {hover && <PortHoverCard info={hover} session={session} />}
    </div>
  );
}

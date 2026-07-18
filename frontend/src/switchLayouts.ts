/** Front-panel layout definitions for virtual switch view. */

export type PortMedia = "rj45" | "sfp" | "sfp+" | "combo";

export interface LayoutSlot {
  /** Front-panel number (matches SNMP ifDescr when numeric). */
  panel: number;
  media: PortMedia;
  /** 0 = top row, 1 = bottom row */
  row: 0 | 1;
  /** Column within section */
  col: number;
  section: "copper" | "fiber";
}

export interface SwitchLayout {
  label: string;
  slots: LayoutSlot[];
  /** Max columns in copper / fiber for grid sizing */
  copperCols: number;
  fiberCols: number;
}

/** How odd/even RJ45 (and combo) ports map to top/bottom rows. */
type CopperPairing = "odd-over-even" | "even-over-odd";

/**
 * Two-high copper pairs.
 * - odd-over-even: top 1,3,5… bottom 2,4,6… (HPE Instant On, generic)
 * - even-over-odd: top 2,4,6… bottom 1,3,5… (many TP-Link JetStream faces)
 */
function copperPaired(count: number, pairing: CopperPairing): LayoutSlot[] {
  const slots: LayoutSlot[] = [];
  const pairs = Math.ceil(count / 2);
  const oddRow: 0 | 1 = pairing === "odd-over-even" ? 0 : 1;
  const evenRow: 0 | 1 = pairing === "odd-over-even" ? 1 : 0;
  for (let i = 0; i < pairs; i++) {
    const odd = 2 * i + 1;
    const even = 2 * i + 2;
    if (odd <= count) {
      slots.push({ panel: odd, media: "rj45", row: oddRow, col: i, section: "copper" });
    }
    if (even <= count) {
      slots.push({ panel: even, media: "rj45", row: evenRow, col: i, section: "copper" });
    }
  }
  return slots;
}

/** Fiber uplinks packed two-high like copper. */
function fiberPacked(
  startPanel: number,
  count: number,
  media: "sfp" | "sfp+",
  pairing: CopperPairing = "odd-over-even"
): LayoutSlot[] {
  const slots: LayoutSlot[] = [];
  const pairs = Math.ceil(count / 2);
  for (let i = 0; i < pairs; i++) {
    const a = startPanel + 2 * i;
    const b = startPanel + 2 * i + 1;
    const aIsOdd = a % 2 === 1;
    const aRow: 0 | 1 =
      pairing === "odd-over-even" ? (aIsOdd ? 0 : 1) : aIsOdd ? 1 : 0;
    const bRow: 0 | 1 = aRow === 0 ? 1 : 0;
    if (2 * i < count) {
      slots.push({ panel: a, media, row: aRow, col: i, section: "fiber" });
    }
    if (2 * i + 1 < count) {
      slots.push({ panel: b, media, row: bRow, col: i, section: "fiber" });
    }
  }
  return slots;
}

function layoutFromParts(
  label: string,
  copper: number,
  fiber: number,
  fiberMedia: "sfp" | "sfp+",
  pairing: CopperPairing = "odd-over-even"
): SwitchLayout {
  const copperSlots = copperPaired(copper, pairing);
  const fiberSlots =
    fiber > 0 ? fiberPacked(copper + 1, fiber, fiberMedia, pairing) : [];
  return {
    label,
    slots: [...copperSlots, ...fiberSlots],
    copperCols: Math.ceil(copper / 2),
    fiberCols: Math.ceil(fiber / 2),
  };
}

/** Same physical footprint for offline / unknown switches. */
export function placeholderLayout(totalPorts?: number): SwitchLayout {
  if (totalPorts === 10) return layoutFromParts("Offline (8+2)", 8, 2, "sfp");
  if (totalPorts === 52) return layoutFromParts("Offline (48+4)", 48, 4, "sfp+");
  if (totalPorts === 28) return layoutFromParts("Offline (24+4)", 24, 4, "sfp+");
  // Default chassis size matches common Instant On 24G + 4SFP+
  return layoutFromParts("Offline", 24, 4, "sfp+");
}

/**
 * Combo ports: one slot per panel (RJ45+SFP rendered in the jack).
 * Identified even-over-odd faces (TP-Link) pair combos like copper.
 * Generic/odd-over-even keeps each combo in its own top-row column.
 */
function layoutWithCombo(
  label: string,
  copperExclusive: number,
  comboCount: number,
  fiberMedia: "sfp" | "sfp+" = "sfp",
  pairing: CopperPairing = "odd-over-even"
): SwitchLayout {
  const slots: LayoutSlot[] = copperPaired(copperExclusive, pairing);
  const startCol = Math.ceil(copperExclusive / 2);
  void fiberMedia;
  if (pairing === "even-over-odd") {
    const pairs = Math.ceil(comboCount / 2);
    const oddRow: 0 | 1 = 1;
    const evenRow: 0 | 1 = 0;
    for (let i = 0; i < pairs; i++) {
      const odd = copperExclusive + 1 + 2 * i;
      const even = copperExclusive + 2 + 2 * i;
      if (2 * i < comboCount) {
        slots.push({
          panel: odd,
          media: "combo",
          row: oddRow,
          col: startCol + i,
          section: "copper",
        });
      }
      if (2 * i + 1 < comboCount) {
        slots.push({
          panel: even,
          media: "combo",
          row: evenRow,
          col: startCol + i,
          section: "copper",
        });
      }
    }
    return {
      label,
      slots,
      copperCols: startCol + pairs,
      fiberCols: 0,
    };
  }
  for (let i = 0; i < comboCount; i++) {
    const panel = copperExclusive + 1 + i;
    slots.push({
      panel,
      media: "combo",
      row: 0,
      col: startCol + i,
      section: "copper",
    });
  }
  return {
    label,
    slots,
    copperCols: startCol + comboCount,
    fiberCols: 0,
  };
}

const MODEL_LAYOUTS: { pattern: RegExp; build: () => SwitchLayout }[] = [
  // Instant On 1930
  {
    pattern: /JL680A|JL681A/i,
    build: () => layoutFromParts("Instant On 1930 8G + 2SFP", 8, 2, "sfp"),
  },
  {
    pattern: /JL682A|JL683/i,
    build: () => layoutFromParts("Instant On 1930 24G + 4SFP+", 24, 4, "sfp+"),
  },
  {
    pattern: /JL684A|JL685|JL686/i,
    build: () => layoutFromParts("Instant On 1930 48G + 4SFP+", 48, 4, "sfp+"),
  },
  // Instant On 1960 — access models include 2×10GBase-T + 2×SFP+ uplinks
  {
    pattern: /JL805A/i,
    build: () => layoutFromParts("Instant On 1960 12×10G + 4SFP+", 12, 4, "sfp+"),
  },
  {
    pattern: /JL806A|JL807A/i,
    build: () =>
      layoutFromParts("Instant On 1960 24G + 2×10G + 2SFP+", 26, 2, "sfp+"),
  },
  {
    pattern: /JL808A|JL809A/i,
    build: () =>
      layoutFromParts("Instant On 1960 48G + 2×10G + 2SFP+", 50, 2, "sfp+"),
  },
  // TP-Link JetStream — odd ports on the bottom row
  {
    pattern: /SG2424|T1600G-28|24-Port Gigabit Smart Switch with 4 Combo/i,
    build: () =>
      layoutWithCombo("TP-Link 24G + 4×combo SFP", 20, 4, "sfp", "even-over-odd"),
  },
];

function inferByPortCount(n: number): SwitchLayout | null {
  if (n === 10) return layoutFromParts("8×RJ45 + 2×SFP", 8, 2, "sfp");
  if (n === 16) return layoutFromParts("12×RJ45 + 4×SFP+", 12, 4, "sfp+");
  if (n === 24) return layoutWithCombo("24×RJ45 (4×combo SFP)", 20, 4);
  if (n === 28) return layoutFromParts("24×RJ45 + 4×SFP+", 24, 4, "sfp+");
  if (n === 52) return layoutFromParts("48×RJ45 + 4×SFP+", 48, 4, "sfp+");
  // Classic combo: N copper + last 2 as combo (e.g. 26 = 24+2 combo)
  if (n === 26) return layoutWithCombo("24×RJ45 + 2×combo", 24, 2);
  if (n === 12) return layoutWithCombo("8×RJ45 + 2×combo", 8, 2);
  return null;
}

function genericTwoHigh(portPanels: number[]): SwitchLayout {
  const sorted = [...portPanels].sort((a, b) => a - b);
  if (
    sorted.length > 0 &&
    sorted[0] === 1 &&
    sorted.every((v, i) => v === i + 1)
  ) {
    return layoutFromParts(`Generic ${sorted.length}-port (2-high)`, sorted.length, 0, "sfp");
  }
  const slots: LayoutSlot[] = [];
  const cols = Math.ceil(sorted.length / 2);
  for (let i = 0; i < cols; i++) {
    if (sorted[2 * i] != null) {
      slots.push({
        panel: sorted[2 * i],
        media: "rj45",
        row: 0,
        col: i,
        section: "copper",
      });
    }
    if (sorted[2 * i + 1] != null) {
      slots.push({
        panel: sorted[2 * i + 1],
        media: "rj45",
        row: 1,
        col: i,
        section: "copper",
      });
    }
  }
  return {
    label: "Generic (2-high)",
    slots,
    copperCols: cols,
    fiberCols: 0,
  };
}

export function resolveSwitchLayout(
  model: string,
  driverId: string,
  panelNumbers: number[]
): SwitchLayout {
  for (const entry of MODEL_LAYOUTS) {
    if (entry.pattern.test(model)) {
      return entry.build();
    }
  }
  const hint = `${driverId} ${model}`;
  if (/hpe_aruba_1960|1960/i.test(hint)) {
    const n = panelNumbers.length;
    // 1960 access faces: N copper incl. 2×10GBase-T + 2×SFP+
    if (n === 28) return layoutFromParts("Instant On 1960 24G + 2×10G + 2SFP+", 26, 2, "sfp+");
    if (n === 52) return layoutFromParts("Instant On 1960 48G + 2×10G + 2SFP+", 50, 2, "sfp+");
    if (n === 16) return layoutFromParts("Instant On 1960 12×10G + 4SFP+", 12, 4, "sfp+");
    const byCount = inferByPortCount(n);
    if (byCount) return byCount;
  }
  if (/hpe_aruba_1930|aruba|1930/i.test(hint)) {
    const byCount = inferByPortCount(panelNumbers.length);
    if (byCount) return byCount;
  }
  if (/tp_link_sg2424|sg2424|t1600g-28|4 combo/i.test(hint)) {
    const n = panelNumbers.length;
    if (n === 28) {
      return layoutFromParts(
        "TP-Link SG2424 24G + 4SFP",
        24,
        4,
        "sfp",
        "even-over-odd"
      );
    }
    return layoutWithCombo(
      "TP-Link 24G + 4×combo SFP",
      20,
      4,
      "sfp",
      "even-over-odd"
    );
  }
  const byCount = inferByPortCount(panelNumbers.length);
  if (byCount) return byCount;
  return genericTwoHigh(panelNumbers);
}

export function panelKey(name: string): number | null {
  const trimmed = name.trim();
  const bare = trimmed.match(/^(\d+)$/);
  if (bare) return Number(bare[1]);
  const slot = trimmed.match(/^\d+\/(\d+)$/);
  if (slot) return Number(slot[1]);
  // Driver-normalized: "Port 21 (combo)", "Port 22 (fiber)"
  const normalized = trimmed.match(/^port\s+(\d+)\b/i);
  if (normalized) return Number(normalized[1]);
  // "port 12: Gigabit Copper", "Port12", "port-12"
  const portLabel = trimmed.match(/^port[\s_-]*(\d+)\b/i);
  if (portLabel) return Number(portLabel[1]);
  // TP-Link combo sides: "gigabit copper 21", "Gigabit Fiber 22"
  const copperFiber = trimmed.match(
    /^(?:gigabit|ten-?gigabit|fast)?\s*(?:copper|fiber|fibre)\s+(?:ethernet\s+)?(?:\d+\/)*(\d+)\s*$/i
  );
  if (copperFiber) return Number(copperFiber[1]);
  // TP-Link / Cisco-style: gigabitEthernet 1/0/12, gi1/0/12, Te1/0/25
  const eth = trimmed.match(
    /(?:gigabit|ten-?gigabit|fast)?\s*ethernet\s+(?:\d+\/)*(\d+)\s*$/i
  );
  if (eth) return Number(eth[1]);
  const short = trimmed.match(/^(?:gi|te|fa)\s*(?:\d+\/)*(\d+)\s*$/i);
  if (short) return Number(short[1]);
  // GigabitEthernet0/12, Ethernet1/0/12 (Cisco IOS — last number)
  const ios = trimmed.match(
    /^(?:gigabit|ten-?gigabit|fast)?ethernet\s*\d+(?:\/\d+)*\/(\d+)$/i
  );
  if (ios) return Number(ios[1]);
  return null;
}

function portPreference(port: { oper_status?: string; name: string; media_mode?: string | null }): number {
  let score = 0;
  if (port.oper_status === "UP") score += 100;
  if (port.media_mode === "fiber") score += 20;
  if (port.media_mode === "copper") score += 10;
  const n = port.name.toLowerCase();
  if (/\bfiber\b|\bfibre\b/.test(n)) score += 5;
  if (/\bcopper\b/.test(n)) score += 2;
  return score;
}

/** Map snapshot ports onto front-panel numbers; fall back to 1..N order. */
export function portsByPanel<T extends { name: string; oper_status?: string; media_mode?: string | null }>(
  ports: T[]
): Map<number, T> {
  const byPanel = new Map<number, T>();
  for (const p of ports) {
    const key = panelKey(p.name);
    if (key == null) continue;
    const existing = byPanel.get(key);
    if (!existing || portPreference(p) > portPreference(existing)) {
      byPanel.set(key, p);
    }
  }
  if (byPanel.size === 0 && ports.length > 0) {
    ports.forEach((p, i) => byPanel.set(i + 1, p));
  }
  return byPanel;
}

import type {
  AppConfig,
  DriverInfo,
  ScanStatus,
  ScenarioDocument,
  ScenarioImportMode,
  ScenarioImportResult,
  SessionState,
  SwitchConfig,
  SwitchSnapshot,
} from "./types";

function apiBase(): string {
  if (import.meta.env.DEV) return "/api";
  const isTauri = "__TAURI_INTERNALS__" in window;
  return isTauri ? "http://127.0.0.1:18742/api" : "/api";
}

function parseApiError(text: string): string {
  try {
    const data = JSON.parse(text) as { detail?: string | { message?: string } };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object" && data.detail.message) {
      return data.detail.message;
    }
  } catch {
    /* not JSON */
  }
  return text;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(parseApiError(text) || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  getConfig: () => request<AppConfig>("/config"),

  patchConfig: (body: Partial<AppConfig>) =>
    request<AppConfig>("/config", { method: "PATCH", body: JSON.stringify(body) }),

  getDrivers: () => request<DriverInfo[]>("/drivers"),

  getDefaultSubnet: () => request<{ cidr: string }>("/subnet/default"),

  listSwitches: () => request<SwitchConfig[]>("/switches"),

  addSwitch: (body: SwitchConfig) =>
    request<SwitchConfig>("/switches", { method: "POST", body: JSON.stringify(body) }),

  updateSwitch: (host: string, body: SwitchConfig) =>
    request<SwitchConfig>("/switches", {
      method: "PUT",
      body: JSON.stringify({ ...body, original_host: host }),
    }),

  deleteSwitch: (host: string) =>
    request<{ deleted: string }>(`/switches/${encodeURIComponent(host)}`, { method: "DELETE" }),

  getSnapshot: (
    host: string,
    refresh = false,
    opts?: { mode?: "full" | "live" | "fast"; prefetch?: boolean }
  ) => {
    const params = new URLSearchParams({ refresh: String(refresh) });
    if (opts?.mode) params.set("mode", opts.mode);
    if (opts?.prefetch === false) params.set("prefetch", "false");
    return request<{ snapshot: SwitchSnapshot; session: SessionState }>(
      `/switches/${encodeURIComponent(host)}/snapshot?${params}`
    );
  },

  getSession: () => request<SessionState>("/session"),

  setHighlight: (vlanId: number | null) =>
    request<SessionState>("/session/highlight", {
      method: "POST",
      body: JSON.stringify({ vlan_id: vlanId }),
    }),

  resolveConflict: (vlanId: number, choice: "session" | "switch") =>
    request<SessionState>("/session/resolve-conflict", {
      method: "POST",
      body: JSON.stringify({ vlan_id: vlanId, choice }),
    }),

  resolveAllConflicts: (choice: "session" | "switch") =>
    request<SessionState>("/session/resolve-conflicts", {
      method: "POST",
      body: JSON.stringify({ choice }),
    }),

  startScan: (body: { cidr?: string; community?: string; snmp_version?: number }) =>
    request<ScanStatus>("/scan", { method: "POST", body: JSON.stringify(body) }),

  getScanStatus: () => request<ScanStatus>("/scan"),

  cancelScan: () => request<ScanStatus>("/scan", { method: "DELETE" }),

  exportScenario: (name = "") => {
    const q = name ? `?name=${encodeURIComponent(name)}` : "";
    return request<ScenarioDocument>(`/scenario${q}`);
  },

  importScenario: (scenario: ScenarioDocument | Record<string, unknown>, mode: ScenarioImportMode) =>
    request<ScenarioImportResult>("/scenario", {
      method: "POST",
      body: JSON.stringify({ scenario, mode }),
    }),
};

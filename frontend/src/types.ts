export interface SwitchConfig {
  host: string;
  community: string;
  /** 1 = v1, 2 = v2c (default), 3 = v3 */
  snmp_version: number;
  name: string;
  driver_id: string;
  port: number;
  /** Reverse-DNS PTR hostname when available (not persisted). */
  dns_name?: string;
  /** SNMPv3 USM fields (used when snmp_version === 3). */
  v3_user?: string;
  v3_auth_proto?: string;
  v3_auth_key?: string;
  v3_priv_proto?: string;
  v3_priv_key?: string;
}

export interface VlanInfo {
  vlan_id: number;
  name: string;
  status?: string;
}

export interface PoePortStatus {
  /** PoE admin enable from PSE table; null if unknown. */
  admin_enable: boolean | null;
  /** disabled | searching | delivering | fault | test | otherFault */
  detection: string | null;
  /** True when actively supplying power. */
  delivering: boolean;
  /** Draw in milliwatts when available. */
  power_mw: number | null;
  /** IEEE class 0–4 when reported. */
  power_class: number | null;
  /** critical | high | low */
  priority: string | null;
}

export interface PortStatus {
  index: number;
  name: string;
  admin_status: string;
  oper_status: string;
  speed_mbps: number | null;
  in_octets: number;
  out_octets: number;
  in_rate_bps: number;
  out_rate_bps: number;
  primary_vlan: number;
  untagged_vlans: number[];
  tagged_vlans: number[];
  /** Active combo/media side when known: "copper" | "fiber". */
  media_mode?: string | null;
  /** Present when the port appears in a PoE PSE table. */
  poe?: PoePortStatus | null;
}

export interface SwitchSnapshot {
  identity: {
    host: string;
    sys_name: string;
    sys_descr: string;
    vendor: string;
    model: string;
    driver_id: string;
  };
  vlans: VlanInfo[];
  ports: PortStatus[];
  timestamp: number;
}

export interface SessionVlan {
  vlan_id: number;
  name: string;
  color: string;
  port_count: number;
  untagged_count: number;
  tagged_count: number;
}

export interface VlanConflict {
  vlan_id: number;
  session_name: string;
  switch_name: string;
  switch_host: string;
  switch_label: string;
}

export interface SessionState {
  vlans: SessionVlan[];
  highlight_vlan: number | null;
  pending_conflicts: VlanConflict[];
}

export interface ScanResult {
  host: string;
  sys_name: string;
  sys_descr: string;
  driver_id: string;
  driver_name: string;
  snmp_ok?: boolean;
}

export interface ScanStatus {
  running: boolean;
  phase?: string;
  ping_done: number;
  ping_total: number;
  snmp_done: number;
  snmp_total: number;
  error: string;
  results: ScanResult[];
}

export interface DriverInfo {
  id: string;
  name: string;
  description: string;
}

export type SwitchOrder = "ip" | "name" | "type";

export interface AppConfig {
  switches: SwitchConfig[];
  switch_order?: SwitchOrder;
  scan_community: string;
  scan_version: number;
  scan_subnet: string;
  poll_interval_sec: number;
  snmp_timeout: number;
  snmp_retries: number;
  snmp_fast_mode: boolean;
  structure_cache_sec: number;
  prefetch_concurrency: number;
}

export type ScenarioImportMode = "replace" | "merge";

export interface ScenarioDocument {
  format?: string;
  version?: number;
  name?: string;
  exported_at?: string;
  switches: SwitchConfig[];
  settings?: Partial<
    Pick<
      AppConfig,
      | "switch_order"
      | "scan_community"
      | "scan_version"
      | "scan_subnet"
      | "poll_interval_sec"
      | "snmp_timeout"
      | "snmp_retries"
      | "snmp_fast_mode"
      | "structure_cache_sec"
      | "prefetch_concurrency"
    >
  >;
}

export interface ScenarioImportSummary {
  mode: ScenarioImportMode;
  name: string;
  switches: number;
  imported: number;
  added: number;
  updated: number;
  removed: number;
}

export interface ScenarioImportResult {
  summary: ScenarioImportSummary;
  config: AppConfig;
}

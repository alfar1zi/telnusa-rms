export type AlarmSeverity = "disaster" | "high" | "average" | "warning" | "information";
export type AlarmStatus   = "active" | "resolved";
export type AlarmState    = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";

export interface AlarmTransition {
  id: number;
  alarm_id: number;
  device_id: string;
  from_state: string | null;
  to_state: string;
  actor: string;
  reason?: string | null;
  timestamp: number;
}

export interface AlarmRule {
  rule_id: string;
  name: string;
  metric: string;
  device_pattern: string;
  severity: AlarmSeverity;
  operator: string;
  threshold?: string | null;
  evaluation_interval: number;
  consecutive_count: number;
  enabled: number;
  description?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface Alarm {
  id: string;
  deviceId: string;
  siteId: string;        // raw site identifier
  siteName: string | null; // null = UNASSIGNED
  problem: string;
  severity: AlarmSeverity;
  status: AlarmStatus;
  state?: AlarmState;
  startTime: number;
  lastTime?: number;
  endTime?: number;
  observedValue?: string | null;
  evidence?: string | null;
  consecutiveOccurrences?: number;
  acknowledgedAt?: number | null;
  acknowledgedBy?: string | null;
  ackNote?: string | null;
  resolvedAt?: number | null;
  resolvedBy?: string | null;
  resolutionReason?: string | null;
  ruleId?: string | null;
  transitions?: AlarmTransition[];
}

export interface FleetSummary {
  total: number;
  online: number;
  degraded: number;
  offline: number;
  unknown: number;
  activeAlarms: number;
  latestTelemetryAt: number | null;
}

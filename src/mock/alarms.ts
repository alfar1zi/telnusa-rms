import type { Alarm } from "@/types/alarm";

export const MOCK_ALARMS: Alarm[] = [
  // RUT-TEST-001: resolved historical alarms
  {
    id: "alm-001", deviceId: "RUT-TEST-001", siteId: "site-a", siteName: "Site A",
    problem: "High temperature warning",
    severity: "warning", status: "resolved",
    startTime: new Date("2026-09-22T10:15:00+07:00").getTime(),
    endTime:   new Date("2026-09-22T10:45:00+07:00").getTime(),
  },
  {
    id: "alm-002", deviceId: "RUT-TEST-001", siteId: "site-a", siteName: "Site A",
    problem: "4G signal degraded",
    severity: "average", status: "resolved",
    startTime: new Date("2026-09-22T14:00:00+07:00").getTime(),
    endTime:   new Date("2026-09-22T14:20:00+07:00").getTime(),
  },
  // RUT-TEST-002: active warning (elevated temperature)
  {
    id: "alm-003", deviceId: "RUT-TEST-002", siteId: "site-b", siteName: "Site B",
    problem: "CPU load elevated (>80%)",
    severity: "warning", status: "active",
    startTime: new Date("2026-09-22T18:30:00+07:00").getTime(),
  },
  {
    id: "alm-004", deviceId: "RUT-TEST-002", siteId: "site-b", siteName: "Site B",
    problem: "RAM usage above threshold",
    severity: "average", status: "active",
    startTime: new Date("2026-09-22T19:00:00+07:00").getTime(),
  },
  // RUT-TEST-003: offline / critical
  {
    id: "alm-005", deviceId: "RUT-TEST-003", siteId: "site-c", siteName: "Site C",
    problem: "Device unreachable",
    severity: "high", status: "active",
    startTime: new Date("2026-09-22T08:05:00+07:00").getTime(),
  },
  {
    id: "alm-006", deviceId: "RUT-TEST-003", siteId: "site-c", siteName: "Site C",
    problem: "Telemetry data missing",
    severity: "average", status: "active",
    startTime: new Date("2026-09-22T08:10:00+07:00").getTime(),
  },
  // RUT-TEST-004: degraded connectivity
  {
    id: "alm-007", deviceId: "RUT-TEST-004", siteId: "site-d", siteName: "Site D",
    problem: "4G signal weak (RSSI < -95 dBm)",
    severity: "warning", status: "active",
    startTime: new Date("2026-09-22T16:45:00+07:00").getTime(),
  },
  {
    id: "alm-008", deviceId: "RUT-TEST-004", siteId: "site-d", siteName: "Site D",
    problem: "PZEM Phase C voltage out of range",
    severity: "average", status: "active",
    startTime: new Date("2026-09-22T20:00:00+07:00").getTime(),
  },
];

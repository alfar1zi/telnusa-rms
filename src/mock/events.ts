import type { DeviceEvent } from "@/types/event";

export const MOCK_EVENTS: DeviceEvent[] = [
  // RUT-TEST-001
  { id: "evt-001", deviceId: "RUT-TEST-001", type: "status_change",  message: "Device came online",               timestamp: new Date("2026-09-22T06:01:00+07:00").getTime() },
  { id: "evt-002", deviceId: "RUT-TEST-001", type: "alarm_created",  message: "High temperature warning fired",   timestamp: new Date("2026-09-22T10:15:00+07:00").getTime() },
  { id: "evt-003", deviceId: "RUT-TEST-001", type: "alarm_resolved", message: "High temperature warning cleared", timestamp: new Date("2026-09-22T10:45:00+07:00").getTime() },
  { id: "evt-004", deviceId: "RUT-TEST-001", type: "alarm_created",  message: "4G signal degraded",               timestamp: new Date("2026-09-22T14:00:00+07:00").getTime() },
  { id: "evt-005", deviceId: "RUT-TEST-001", type: "alarm_resolved", message: "4G signal recovered",              timestamp: new Date("2026-09-22T14:20:00+07:00").getTime() },

  // RUT-TEST-002
  { id: "evt-010", deviceId: "RUT-TEST-002", type: "status_change",  message: "Device came online",               timestamp: new Date("2026-09-22T06:05:00+07:00").getTime() },
  { id: "evt-011", deviceId: "RUT-TEST-002", type: "alarm_created",  message: "CPU load elevated",                timestamp: new Date("2026-09-22T18:30:00+07:00").getTime() },
  { id: "evt-012", deviceId: "RUT-TEST-002", type: "alarm_created",  message: "RAM usage above threshold",        timestamp: new Date("2026-09-22T19:00:00+07:00").getTime() },

  // RUT-TEST-003
  { id: "evt-020", deviceId: "RUT-TEST-003", type: "status_change",  message: "Device came online",               timestamp: new Date("2026-09-22T06:00:00+07:00").getTime() },
  { id: "evt-021", deviceId: "RUT-TEST-003", type: "status_change",  message: "Device went offline",              timestamp: new Date("2026-09-22T08:05:00+07:00").getTime() },
  { id: "evt-022", deviceId: "RUT-TEST-003", type: "alarm_created",  message: "Device unreachable",               timestamp: new Date("2026-09-22T08:05:00+07:00").getTime() },

  // RUT-TEST-004
  { id: "evt-030", deviceId: "RUT-TEST-004", type: "status_change",  message: "Device came online",               timestamp: new Date("2026-09-22T06:10:00+07:00").getTime() },
  { id: "evt-031", deviceId: "RUT-TEST-004", type: "alarm_created",  message: "4G signal weak",                   timestamp: new Date("2026-09-22T16:45:00+07:00").getTime() },
  { id: "evt-032", deviceId: "RUT-TEST-004", type: "alarm_created",  message: "PZEM Phase C voltage out of range",timestamp: new Date("2026-09-22T20:00:00+07:00").getTime() },
];

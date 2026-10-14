import type { AcquisitionState, ConnectivityState, DataAge, DeviceStatus, SensorState, UserRole } from "@/types/device";

export type AuthProvider = "local";

export interface AuthUser {
  id:                  string;
  email:               string;
  name:                string;
  role:                UserRole;
  provider:            AuthProvider;
  mustChangePassword?: boolean;
  disabled?:           boolean;
  createdAt?:          number;
  updatedAt?:          number;
}

export interface AuthProviders {
  providers: {
    local: { enabled: boolean };
  };
}

export interface UserRecord {
  id:                 string;
  email:              string;
  name:               string;
  role:               UserRole;
  provider:           AuthProvider;
  mustChangePassword: boolean;
  disabled:           boolean;
  createdAt:          number;
  updatedAt:          number;
}

export interface CreateUserPayload {
  email:    string;
  name:     string;
  password: string;
  role:     UserRole;
}

export interface UpdateUserPayload {
  name?:               string;
  role?:               UserRole;
  disabled?:           boolean;
  mustChangePassword?: boolean;
}

// SSE event shapes from /api/live/:deviceId
// "resync" is not a device state: it is the server telling the client that its
// buffered view was truncated and the authoritative snapshot must be refetched.
export type SseEventType = "telemetry" | "status" | "health" | "alarm" | "keepalive" | "resync";

export interface SseResyncPayload {
  reason: "buffer_overflow";
}

// Why the live stream is not delivering. Kept distinct so the UI never renders
// "device offline" when the device is fine and only the stream was refused.
export type LiveStreamState =
  | "connected"
  | "reconnecting"
  | "resyncing";   // buffer overflowed, snapshot refetch in flight

export interface SseTelemetryPayload {
  deviceId:  string;
  timestamp: number;
  dataAge:   DataAge;
  mppt:      Record<string, number | null>;
  pzem:      Record<string, number | null>;
  // Solar snapshot in W/m² (1:1, matches DeviceLatestTelemetry.solar).
  // Absent on older frames; never assume presence.
  solar?: { radiation: number | null } | null;
  derived?: { pvInputPower: number | null; outputPower: number | null } | null;
}

export interface SseStatusPayload {
  deviceId:     string;
  status:       DeviceStatus;
  connectivity: ConnectivityState;
  dataAge:      DataAge;
  lastSeen:     number | null;
  clockSkew?:        boolean;
  clockSkewSeconds?: number | null;
  mpptState?:        SensorState;
  pzemState?:        SensorState;
  sensor3State?:     SensorState;
  acquisitionState?: AcquisitionState;
}

export interface SseHealthPayload {
  deviceId:         string;
  cpuPercent:       number | null;
  ramPercent:       number | null;
  temperatureC:     number | null;
  rssi:             number | null;
  lastTelemetryAge: number | null;
}

export interface SseEvent {
  type:    SseEventType;
  payload: SseTelemetryPayload | SseStatusPayload | SseHealthPayload
         | SseResyncPayload | Record<string, never>;
}

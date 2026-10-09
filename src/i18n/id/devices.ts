import type { Dict } from "../types";

/**
 * Halaman Device: registri fleet, filter, tabel, dan kegagalan pemuatan.
 *
 * Token status kapital ("NO DATA", "UNKNOWN") adalah kosakata console dan sama
 * di kedua bahasa, jadi tidak dijadikan key di sini.
 */
const slice: Dict = {
  "devices.title": "Device",
  "devices.subtitle": "Gateway RUT906 terdaftar, kesehatan fleet, dan telemetri jarak jauh",

  "devices.kpi.total": "Total Gateway",
  "devices.kpi.online": "Online",
  "devices.kpi.degraded": "Menurun / Basi",
  "devices.kpi.offline": "Offline",
  "devices.kpi.alarms": "Alarm Aktif",

  "devices.empty": "Belum ada device terdaftar.",
  "devices.error.title": "Device Tidak Tersedia",
  "devices.error.alarmsTitle": "Data Alarm Tidak Tersedia",
  "devices.error.alarmsMessage": "Daftar device berhasil dimuat, tetapi jumlah alarm tidak dapat diambil. Jumlahnya tidak diketahui, bukan nol.",

  "devices.col.site": "Site",
  "devices.col.deviceId": "ID Device",
  "devices.col.model": "Model",
  "devices.col.status": "Status",
  "devices.col.health": "Kesehatan",
  "devices.col.network": "Jaringan",
  "devices.col.lastSeen": "Terakhir Terlihat",
  "devices.col.alarms": "Alarm",
  "devices.col.actions": "Aksi",

  "devices.action.monitor": "Monitor",
  "devices.action.details": "Detail",

  "devices.load.network": "Jaringan tidak terjangkau - API tidak dapat dihubungi.",
  "devices.load.unauthenticated": "Sesi berakhir atau belum terautentikasi. Silakan masuk kembali.",
  "devices.load.forbidden": "Tidak berwenang melihat device.",
  "devices.load.notFound": "Endpoint registri device tidak ditemukan.",
  "devices.load.rateLimited": "Dibatasi oleh API. Coba lagi sebentar lagi.",
  "devices.load.server": "Backend tidak tersedia (HTTP {status}).",
  "devices.load.rejected": "Permintaan ditolak (HTTP {status}).",
};

export default slice;

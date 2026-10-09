import type { Dict } from "../types";

/**
 * Halaman alarm, tabel alarm, tabel event, dan dialog aksi alarm.
 *
 * Token siklus hidup (OPEN / ACK'D / RESOLVED) tetap huruf besar di kedua
 * bahasa: itu kosakata status konsol yang dibaca sebagai chip berwarna, bukan
 * kalimat. Kata tingkat dipakai bersama badge lewat `badge.severity.*` di core,
 * begitu pula `state.noData` dan `common.cancel`, supaya satu kata tidak ditulis
 * dua kali.
 */
const alarms: Dict = {
  // Kerangka halaman
  "alarms.title": "Alarm",
  "alarms.subtitle": "Rekayasa alarm baku, pengelolaan siklus hidup, dan evaluasi aturan",
  "alarms.unavailable": "Alarm Tidak Tersedia",
  "alarms.filterPlaceholder": "Filter masalah, device...",
  "alarms.allDevices": "Semua Device",
  "alarms.allGateways": "Semua Gateway",
  "alarms.allSeverities": "Semua Tingkat",
  "alarms.actionFailed": "Aksi gagal",

  // Suara alarm
  "alarms.sound.on": "Suara Alarm Aktif",
  "alarms.sound.off": "Suara Alarm Nonaktif",

  // Tab
  "alarms.tab.active": "Alarm Aktif ({count})",
  "alarms.tab.history": "Riwayat Selesai ({count})",
  "alarms.tab.rules": "Aturan Alarm ({count})",

  // Token status siklus hidup
  "alarms.state.open": "TERBUKA",
  "alarms.state.acknowledged": "DI-ACK",
  "alarms.state.resolved": "SELESAI",

  // Tabel alarm
  "alarms.table.triggered": "Dipicu / Terakhir Terlihat",
  "alarms.table.device": "Device",
  "alarms.table.site": "Site",
  "alarms.table.problem": "Masalah & Konteks",
  "alarms.table.severity": "Tingkat",
  "alarms.table.state": "Status",
  "alarms.table.actions": "Aksi",
  "alarms.table.firstSeen": "Pertama: {time}",
  "alarms.table.ackBy": "Di-ACK oleh {operator}",
  "alarms.table.ackByNote": "Di-ACK oleh {operator} (\"{note}\")",
  "alarms.table.resolvedBy": "Diselesaikan oleh {operator}",
  "alarms.table.resolvedByNote": "Diselesaikan oleh {operator} (\"{note}\")",
  "alarms.empty.noMatch": "Tidak ada alarm yang cocok dengan kriteria.",
  "alarms.empty.none": "Tidak ada alarm. Semua perangkat yang dipantau melapor dalam batasnya.",
  "alarms.empty.history": "Belum ada alarm yang tercatat selesai.",

  // Aksi per baris
  "alarms.action.ack": "ACK",
  "alarms.action.resolve": "Selesaikan",

  // Tab aturan mesin
  "alarms.rules.enabled": "AKTIF",
  "alarms.rules.disabled": "NONAKTIF",
  "alarms.rules.summary": "ID Aturan: {id} • Metrik: {metric} {operator} {threshold} (lingkup: {pattern})",
  "alarms.rules.schedule": "Interval: {interval}s • Berturut-turut: {consecutive}",
  "alarms.rules.noThreshold": "TIDAK ADA",

  // Dialog ACK / selesai
  "alarms.dialog.ackTitle": "ACK Alarm",
  "alarms.dialog.resolveTitle": "Selesaikan Alarm",
  "alarms.dialog.subject": "{problem} pada {deviceId}",
  "alarms.dialog.ackNoteLabel": "Catatan Operator (opsional)",
  "alarms.dialog.resolveReasonLabel": "Alasan Penyelesaian (opsional)",
  "alarms.dialog.ackNotePlaceholder": "mis. Memeriksa terminal baterai...",
  "alarms.dialog.resolveReasonPlaceholder": "mis. Selesai setelah kabel dipasang ulang",
  "alarms.dialog.confirmAck": "Konfirmasi ACK",
  "alarms.dialog.confirmResolve": "Konfirmasi Selesai",
  "alarms.dialog.processing": "Memproses...",

  // Tabel event perangkat
  "alarms.events.time": "Waktu",
  "alarms.events.type": "Jenis",
  "alarms.events.message": "Pesan",
  "alarms.events.empty": "Tidak ada event tercatat.",
};

export default alarms;

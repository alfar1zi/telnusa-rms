import type { Dict } from "../types";

/**
 * Halaman dashboard: bar KPI armada, kartu utama perangkat, grid subsistem,
 * alarm, dan event terbaru.
 *
 * Istilah baku NOC tetap dipakai (site, PV, MPPT, AC, DC, kWh, edge). Token
 * status bersama ("Loading", "NO DATA", "UNKNOWN") diambil dari slice core.
 */
const dashboard: Dict = {
  "dashboard.title": "Komando Operasi",
  "dashboard.subtitle": "Telemetri energi terbarukan dan status site edge secara waktu nyata",
  "dashboard.fleetUnavailable": "Telemetri Armada Tidak Tersedia",
  "dashboard.selectDevice": "Pilih Perangkat",
  "dashboard.details": "Detail",

  // Bar KPI armada
  "dashboard.kpi.online": "Online",
  "dashboard.kpi.attention": "Perlu Perhatian",
  "dashboard.kpi.offline": "Offline",

  // Kartu utama perangkat
  "dashboard.hero.siteState": "Status Site & Edge",
  "dashboard.hero.lastSeen": "Terakhir Terlihat",
  "dashboard.hero.pvGeneration": "Pembangkitan PV",
  "dashboard.hero.stale": "Basi",
  "dashboard.hero.pvPowerNote": "Masukan daya PV surya sesaat secara waktu nyata",
  "dashboard.hero.arrayVoltage": "Tegangan Panel",
  "dashboard.hero.arrayCurrent": "Arus Panel",
  "dashboard.hero.accumulatedEnergy": "Energi Terakumulasi",
  "dashboard.hero.energyNote": "Total energi yang diukur oleh meter daya AC",

  // Panel surya dan konversi MPPT
  "dashboard.mppt.title": "Panel Surya & MPPT",
  "dashboard.status.title": "Status armada",
  "dashboard.model.title": "Model perangkat",
  "dashboard.alarms.breakdownTitle": "Alarm armada",
  "dashboard.alarms.none": "Tidak ada alarm aktif",
  "dashboard.mppt.arrayPower": "Daya array",
  "dashboard.mppt.outputPower": "Daya keluaran",
  "dashboard.mppt.subtitle": "Telemetri konversi daya DC dan keluaran MPPT",
  "dashboard.mppt.pvVoltage": "Tegangan PV",
  "dashboard.mppt.pvCurrent": "Arus PV",
  "dashboard.mppt.outVoltage": "Tegangan Keluaran",
  "dashboard.mppt.outCurrent": "Arus Keluaran",
  "dashboard.mppt.irradiance": "Iradiasi Surya",
  "dashboard.mppt.historicalTrends": "Tren Historis",

  // Meter daya AC 3 fasa
  "dashboard.ac.title": "Pemantauan Daya AC 3 Fasa",
  "dashboard.ac.subtitle": "Tegangan tiga fasa, arus beban, dan kWh kumulatif",
  "dashboard.ac.phaseA": "Fasa A",
  "dashboard.ac.phaseB": "Fasa B",
  "dashboard.ac.phaseC": "Fasa C",
  "dashboard.ac.voltage": "Tegangan",
  "dashboard.ac.current": "Arus",
  "dashboard.ac.energyReports": "Laporan Energi",

  // Alarm aktif
  "dashboard.alarms.title": "Alarm Sistem Aktif",
  "dashboard.alarms.subtitle": "Masalah waktu nyata untuk cakupan {site}",
  "dashboard.alarms.siteFallback": "site terpilih",
  "dashboard.alarms.activeCount": "{count} Aktif",

  // Event terbaru
  "dashboard.events.title": "Event Perangkat Terbaru",
  "dashboard.events.subtitle": "Transisi telemetri dan notifikasi edge",
  "dashboard.events.all": "Semua Event",
};

export default dashboard;

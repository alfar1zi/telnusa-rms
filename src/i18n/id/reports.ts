import type { Dict } from "../types";

/**
 * Halaman laporan historis: preset periode, pemilihan metrik, chart per metrik,
 * ringkasan KPI, tabel record, dan aksi export. Satuan (W, A, V, kWh, W/m2) dan
 * baris header CSV/XLSX adalah data, bukan copy, jadi tidak masuk kamus.
 */
const reports: Dict = {
  "reports.title": "Laporan & Analitik",
  "reports.subtitle": "Penarikan telemetri historis dengan chart per metrik dan keluaran Excel, CSV, atau PDF",

  // Form query
  "reports.filter.device": "Perangkat",
  "reports.filter.devicePlaceholder": "Pilih Perangkat",
  "reports.filter.range": "Periode",
  "reports.filter.metrics": "Metrik",
  "reports.filter.metricsSelected": "{count} dipilih",
  "reports.filter.startDate": "Tanggal Mulai",
  "reports.filter.endDate": "Tanggal Selesai",

  // Preset periode
  "reports.range.daily": "Harian (hari ini)",
  "reports.range.weekly": "Mingguan (minggu ini)",
  "reports.range.monthly": "Bulanan (bulan ini)",
  "reports.range.yearly": "Tahunan (tahun ini)",
  "reports.range.custom": "Rentang kustom",

  // Grup metrik
  "reports.category.mppt": "Solar DC dan MPPT",
  "reports.category.pzem": "AC 3 fasa dan energi",
  "reports.category.solar": "Iradiansi",

  // Aksi
  "reports.action.querying": "Mengambil data…",
  "reports.action.generate": "Tampilkan",
  "reports.action.exportCsv": "Export CSV",
  "reports.action.exportXlsx": "Export Excel",
  "reports.action.print": "Cetak atau PDF",

  // Status dan error
  "reports.error.dateRange": "Tanggal mulai harus lebih awal atau sama dengan tanggal selesai.",
  "reports.error.noMetric": "Pilih minimal satu metrik.",
  "reports.error.fetch": "Gagal mengambil riwayat telemetri untuk perangkat yang dipilih.",
  "reports.truncated": "Query mengembalikan rentang terbatas (limit {limit} sampel). Menampilkan record telemetri yang otoritatif.",
  "reports.empty.initial": "Pilih perangkat, periode, dan metrik untuk menarik record historis.",
  "reports.empty.noPoints": "Tidak ada titik telemetri untuk perangkat dan parameter yang dipilih.",

  // KPI ringkasan
  "reports.kpi.samples": "Sampel",
  "reports.kpi.minimum": "Minimum",
  "reports.kpi.startCounter": "Counter Awal",
  "reports.kpi.maximum": "Maksimum",
  "reports.kpi.endCounter": "Counter Akhir",
  "reports.kpi.average": "Rata-rata",
  "reports.kpi.periodDelta": "Delta Periode",

  // Ringkasan per metrik (pilihan banyak metrik)
  "reports.summary.title": "Ringkasan per metrik",
  "reports.summary.metric": "Metrik",
  "reports.summary.samples": "Sampel",
  "reports.summary.min": "Min",
  "reports.summary.avg": "Rata-rata / delta",
  "reports.summary.max": "Maks",

  // Tabel record
  "reports.table.summaryMulti": "{metrics} metrik · {count} baris",
  "reports.table.dateTime": "Tanggal & Waktu (WIB)",

  // Nama metrik (satuan ditambahkan di pemanggil)
  "reports.metric.pvInputPower": "Daya Masuk PV",
  "reports.metric.outputPower": "Daya Keluaran MPPT",
  "reports.metric.pvVoltage": "Tegangan Panel PV",
  "reports.metric.pvCurrent": "Arus Panel PV",
  "reports.metric.outputVoltage": "Tegangan Keluaran MPPT",
  "reports.metric.outputCurrent": "Arus Keluaran MPPT",
  "reports.metric.phaseAVoltage": "Tegangan Fasa A",
  "reports.metric.phaseBVoltage": "Tegangan Fasa B",
  "reports.metric.phaseCVoltage": "Tegangan Fasa C",
  "reports.metric.phaseACurrent": "Arus Fasa A",
  "reports.metric.phaseBCurrent": "Arus Fasa B",
  "reports.metric.phaseCCurrent": "Arus Fasa C",
  "reports.metric.energy": "Energi Kumulatif",
  "reports.metric.radiation": "Iradiansi Surya",
};

export default reports;

import type { Dict } from "../types";

/**
 * Halaman Monitoring: konsol analitik telemetri.
 *
 * Deskripsi chart tidak ada di sini: kunci-nya mengikuti path metrik di ruang
 * bersama `chart.metric.*` / `chart.scope.*` pada core, supaya deskripsi tidak
 * pernah menyimpang dari chart yang dijelaskannya.
 */
const slice: Dict = {
  "monitoring.subtitle": "Analisis sensor deret waktu resolusi tinggi & profil kelistrikan",
  "monitoring.deviceLabel": "Device:",

  "monitoring.view.workspace": "Ringkasan Operasional",
  "monitoring.view.metrics": "Rincian Semua Metrik",

  "monitoring.kpi.todayEnergy": "Energi Hari Ini",
  "monitoring.kpi.todayEnergyNote": "Asia/Jakarta 00:00 - Sekarang",
  "monitoring.kpi.periodDelta": "Delta Energi Periode",
  "monitoring.kpi.periodDeltaNote": "Interval {range} terpilih",
  "monitoring.kpi.accumulated": "Meter Akumulatif",
  "monitoring.kpi.accumulatedNote": "Register penghitung total",
  "monitoring.kpi.resolutionQuality": "Resolusi & Kualitas",
  "monitoring.kpi.coverageAbbrev": "cakupan",
  "monitoring.kpi.quality": "Kualitas: {quality}",

  "monitoring.error.title": "Riwayat Telemetri Tidak Tersedia",
  "monitoring.error.devices": "Gagal memuat daftar device",
  "monitoring.error.telemetry": "Gagal memuat riwayat telemetri",

  "monitoring.banner.head": "Data tersedia untuk {duration} dari {range} terpilih ({coverage}% cakupan waktu",
  "monitoring.banner.sampleCoverage": ", {coverage}% cakupan sampel",
  "monitoring.banner.gap": ", {count} gap data terdeteksi",
  "monitoring.banner.gaps": ", {count} gap data terdeteksi",
  "monitoring.banner.truncated": ", pengambilan riwayat mencapai batas halaman sebelum awal jendela",
  "monitoring.banner.tail": ").",

  "monitoring.section.solarDc": "Konversi Daya DC Surya & MPPT",
  "monitoring.section.acThreePhase": "Subsistem Kelistrikan AC 3 Fasa",

  "monitoring.chart.powerDc": "Daya Output PV Surya vs MPPT",
  "monitoring.chart.voltageDc": "Tegangan DC PV Surya vs MPPT",
  "monitoring.chart.voltageAc": "Profil Tegangan 3 Fasa",
  "monitoring.chart.currentAc": "Profil Arus Beban 3 Fasa",
  "monitoring.chart.accumulatedEnergy": "Meter Energi Akumulatif",

  "monitoring.metric.pvInputPower": "Daya Input PV",
  "monitoring.metric.mpptOutputPower": "Daya Output MPPT",
  "monitoring.metric.pvVoltage": "Tegangan Array PV",
  "monitoring.metric.pvCurrent": "Arus Array PV",
  "monitoring.metric.mpptOutputVoltage": "Tegangan Output MPPT",
  "monitoring.metric.mpptOutputCurrent": "Arus Output MPPT",
  "monitoring.metric.phaseAVoltage": "Tegangan Fasa A",
  "monitoring.metric.phaseBVoltage": "Tegangan Fasa B",
  "monitoring.metric.phaseCVoltage": "Tegangan Fasa C",
  "monitoring.metric.phaseACurrent": "Arus Fasa A",
  "monitoring.metric.phaseBCurrent": "Arus Fasa B",
  "monitoring.metric.phaseCCurrent": "Arus Fasa C",
  "monitoring.metric.accumulatedEnergy": "Energi Akumulatif",
  "monitoring.metric.solarRadiation": "Iradiansi Surya",
};

export default slice;

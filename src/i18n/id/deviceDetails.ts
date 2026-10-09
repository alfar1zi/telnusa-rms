import type { Dict } from "../types";

/**
 * Halaman detail perangkat: header, tab, kartu KPI, baris metadata, kartu per
 * sensor dan tabel transport Modbus.
 *
 * Deskripsi chart TIDAK ada di sini: itu copy chart bersama yang berkunci path
 * metrik (`chart.metric.*` / `chart.scope.*`) dan tetap di core.
 */
const slice: Dict = {
  // Header, aksi jarak jauh dan nilai cadangan
  "deviceDetails.loadFailed": "Perangkat tidak dapat dimuat dari backend.",
  "deviceDetails.notFound": "Perangkat tidak ditemukan.",
  "deviceDetails.header.serial": "Serial:",
  "deviceDetails.header.site": "Site:",
  "deviceDetails.action.nativeWebui": "WebUI Native",
  "deviceDetails.action.sshTerminal": "Terminal SSH",
  "deviceDetails.value.modelFallback": "Teltonika RUT906",

  // Tab
  "deviceDetails.tab.overview": "Ikhtisar",
  "deviceDetails.tab.power": "Surya & MPPT",
  "deviceDetails.tab.energy": "AC & Energi",
  "deviceDetails.tab.diagnostics": "Diagnostik & Modbus",
  "deviceDetails.tab.alarms": "Alarm & Event",
  "deviceDetails.tab.remote": "Akses Jarak Jauh",

  // Kartu KPI ikhtisar
  "deviceDetails.kpi.pvGeneration": "Produksi PV",
  "deviceDetails.kpi.pvGenerationNote": "Masukan DC panel surya sesaat",
  "deviceDetails.kpi.energy": "Energi Kumulatif",
  "deviceDetails.kpi.energyNote": "Konsumsi grid/beban AC kumulatif",
  "deviceDetails.kpi.health": "Kesehatan Perangkat",
  "deviceDetails.kpi.lastSeen": "Terakhir terlihat {value}",

  // Kartu metadata ikhtisar
  "deviceDetails.card.identity": "Identitas & Konektivitas Edge",
  "deviceDetails.card.health": "Kesehatan Operasional & Telemetri",
  "deviceDetails.field.site": "Site",
  "deviceDetails.field.deviceModel": "Model Perangkat",
  "deviceDetails.field.serialNumber": "Nomor Seri",
  "deviceDetails.field.networkType": "Tipe Jaringan",
  "deviceDetails.field.connectivity": "Konektivitas",
  "deviceDetails.field.telemetryStatus": "Status Telemetri",
  "deviceDetails.field.systemHealth": "Kesehatan Sistem",
  "deviceDetails.field.lastTelemetry": "Telemetri Terakhir",
  "deviceDetails.field.mpptAcquisition": "Akuisisi MPPT",
  "deviceDetails.field.pzemAcquisition": "Akuisisi PZEM",
  "deviceDetails.field.solarAcquisition": "Akuisisi Iradiasi Surya",

  // Kartu sensor dan seri chart
  "deviceDetails.device.mpptController": "MPPT IPANDEE",
  "deviceDetails.device.acMeter": "Meter Daya AC PZEM-6L24",
  "deviceDetails.metric.pvVoltage": "Tegangan PV",
  "deviceDetails.metric.pvCurrent": "Arus PV",
  "deviceDetails.metric.outputVoltage": "Tegangan Output",
  "deviceDetails.metric.outputCurrent": "Arus Output",
  "deviceDetails.metric.solarRadiation": "Iradiasi Surya",
  "deviceDetails.metric.phaseAVoltage": "Tegangan Fasa A",
  "deviceDetails.metric.phaseBVoltage": "Tegangan Fasa B",
  "deviceDetails.metric.phaseCVoltage": "Tegangan Fasa C",
  "deviceDetails.metric.phaseACurrent": "Arus Fasa A",
  "deviceDetails.metric.phaseBCurrent": "Arus Fasa B",
  "deviceDetails.metric.phaseCCurrent": "Arus Fasa C",
  "deviceDetails.metric.energy": "Energi",
  "deviceDetails.metric.acVoltages": "Tegangan AC",
  "deviceDetails.phase.a": "Fasa A",
  "deviceDetails.phase.b": "Fasa B",
  "deviceDetails.phase.c": "Fasa C",

  // Judul kartu chart (satuan menjadi bagian judul)
  "deviceDetails.chart.pvVoltage": "Tegangan PV (V)",
  "deviceDetails.chart.pvCurrent": "Arus PV (A)",
  "deviceDetails.chart.outputVoltage": "Tegangan Output (V)",
  "deviceDetails.chart.outputCurrent": "Arus Output (A)",
  "deviceDetails.chart.acVoltages": "Tegangan AC 3 Fasa (V)",
  "deviceDetails.chart.energyTrend": "Tren Energi Kumulatif (kWh)",

  // Diagnostik: peta Modbus dan transport telemetri
  "deviceDetails.card.modbus": "Konfigurasi Modbus RS-485",
  "deviceDetails.card.transport": "Transport Telemetri & Kesehatan Edge",
  "deviceDetails.field.serialInterface": "Antarmuka Serial",
  "deviceDetails.field.baudRate": "Baud Rate & Paritas",
  "deviceDetails.field.mpptController": "Kontroler MPPT",
  "deviceDetails.field.acMeter": "Meter Daya AC",
  "deviceDetails.field.radiationSensor": "Sensor Iradiasi Surya",
  "deviceDetails.field.mqttState": "Status Ingestion MQTT",
  "deviceDetails.field.cellularSignal": "Sinyal Seluler",
  "deviceDetails.field.mobileNetwork": "Jaringan Seluler",
  "deviceDetails.field.modemTemp": "Suhu Modem",
  "deviceDetails.field.memoryUsage": "Penggunaan Memori",
  "deviceDetails.field.flashUsage": "Penggunaan Flash",
  "deviceDetails.field.cpuLoad": "Beban CPU",
  "deviceDetails.field.edgeUptime": "Uptime Edge",
  "deviceDetails.field.senderPeriod": "Periode Data Sender",
  "deviceDetails.field.healthSource": "Integrasi Sumber Kesehatan",
  "deviceDetails.value.serialInterface": "/dev/rs485 → /dev/ttyS1",
  "deviceDetails.value.baudRate": "9600 bps, 8N1, Tanpa Paritas",
  "deviceDetails.value.mpptUnit": "ID Unit: 2 (IPANDEE)",
  "deviceDetails.value.meterUnit": "ID Unit: 1, FC04 (PZEM-6L24)",
  "deviceDetails.value.radiationSensor": "Honde RD-PSR-01 (RS485, slave 3)",
  "deviceDetails.value.senderPeriod": "20 detik (format Lua)",
  "deviceDetails.value.cpuLoadDetail": " · beban {load} / {cores} core",

  // Tab alarm
  "deviceDetails.alarms.active": "Alarm Aktif",
  "deviceDetails.alarms.events": "Riwayat Event Perangkat",
};

export default slice;

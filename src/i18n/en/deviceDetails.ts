import type { Dict } from "../types";

/**
 * Device detail page: header, tabs, KPI cards, metadata rows, per-sensor cards
 * and the Modbus transport table.
 *
 * Chart descriptions are NOT here: they are shared chart copy keyed by metric
 * path (`chart.metric.*` / `chart.scope.*`) and stay in core.
 */
const slice: Dict = {
  // Header, remote actions and fallbacks
  "deviceDetails.loadFailed": "The device could not be loaded from the backend.",
  "deviceDetails.notFound": "Device not found.",
  "deviceDetails.header.serial": "Serial:",
  "deviceDetails.header.site": "Site:",
  "deviceDetails.action.nativeWebui": "Native WebUI",
  "deviceDetails.action.sshTerminal": "SSH Terminal",
  "deviceDetails.value.modelFallback": "Teltonika RUT906",

  // Tabs
  "deviceDetails.tab.overview": "Overview",
  "deviceDetails.tab.power": "Solar & MPPT",
  "deviceDetails.tab.energy": "AC & Energy",
  "deviceDetails.tab.diagnostics": "Diagnostics & Modbus",
  "deviceDetails.tab.alarms": "Alarms & Events",
  "deviceDetails.tab.remote": "Remote Access",

  // Overview KPI cards
  "deviceDetails.kpi.pvGeneration": "PV Generation",
  "deviceDetails.kpi.pvGenerationNote": "Instantaneous solar DC input",
  "deviceDetails.kpi.energy": "Accumulated Energy",
  "deviceDetails.kpi.energyNote": "Cumulative AC grid/load consumption",
  "deviceDetails.kpi.health": "Device Health",
  "deviceDetails.kpi.lastSeen": "Last seen {value}",

  // Overview metadata cards
  "deviceDetails.card.identity": "Identity & Edge Connectivity",
  "deviceDetails.card.health": "Operational Health & Telemetry",
  "deviceDetails.field.site": "Site",
  "deviceDetails.field.deviceModel": "Device Model",
  "deviceDetails.field.serialNumber": "Serial Number",
  "deviceDetails.field.networkType": "Network Type",
  "deviceDetails.field.connectivity": "Connectivity",
  "deviceDetails.field.telemetryStatus": "Telemetry Status",
  "deviceDetails.field.systemHealth": "System Health",
  "deviceDetails.field.lastTelemetry": "Last Telemetry",
  "deviceDetails.field.mpptAcquisition": "MPPT Acquisition",
  "deviceDetails.field.pzemAcquisition": "PZEM Acquisition",
  "deviceDetails.field.solarAcquisition": "Solar Irradiance Acquisition",

  // Sensor cards and chart series
  "deviceDetails.device.mpptController": "MPPT IPANDEE",
  "deviceDetails.device.acMeter": "PZEM-6L24 AC Power Meter",
  "deviceDetails.metric.pvVoltage": "PV Voltage",
  "deviceDetails.metric.pvCurrent": "PV Current",
  "deviceDetails.metric.outputVoltage": "Output Voltage",
  "deviceDetails.metric.outputCurrent": "Output Current",
  "deviceDetails.metric.solarRadiation": "Solar Irradiance",
  "deviceDetails.metric.phaseAVoltage": "Phase A Volt",
  "deviceDetails.metric.phaseBVoltage": "Phase B Volt",
  "deviceDetails.metric.phaseCVoltage": "Phase C Volt",
  "deviceDetails.metric.phaseACurrent": "Phase A Amp",
  "deviceDetails.metric.phaseBCurrent": "Phase B Amp",
  "deviceDetails.metric.phaseCCurrent": "Phase C Amp",
  "deviceDetails.metric.energy": "Energy",
  "deviceDetails.metric.acVoltages": "AC Voltages",
  "deviceDetails.phase.a": "Phase A",
  "deviceDetails.phase.b": "Phase B",
  "deviceDetails.phase.c": "Phase C",

  // Chart card titles (the unit is part of the title)
  "deviceDetails.chart.pvVoltage": "PV Voltage (V)",
  "deviceDetails.chart.pvCurrent": "PV Current (A)",
  "deviceDetails.chart.outputVoltage": "Output Voltage (V)",
  "deviceDetails.chart.outputCurrent": "Output Current (A)",
  "deviceDetails.chart.acVoltages": "3-Phase AC Voltages (V)",
  "deviceDetails.chart.energyTrend": "Cumulative Energy Trend (kWh)",

  // Diagnostics: Modbus map and telemetry transport
  "deviceDetails.card.modbus": "Modbus RS-485 Configuration",
  "deviceDetails.card.transport": "Telemetry Transport & Edge Health",
  "deviceDetails.field.serialInterface": "Serial Interface",
  "deviceDetails.field.baudRate": "Baud Rate & Parity",
  "deviceDetails.field.mpptController": "MPPT Controller",
  "deviceDetails.field.acMeter": "AC Power Meter",
  "deviceDetails.field.radiationSensor": "Solar Irradiance Sensor",
  "deviceDetails.field.mqttState": "MQTT Ingestion State",
  "deviceDetails.field.cellularSignal": "Cellular Signal",
  "deviceDetails.field.mobileNetwork": "Mobile Network",
  "deviceDetails.field.modemTemp": "Modem Temperature",
  "deviceDetails.field.memoryUsage": "Memory Usage",
  "deviceDetails.field.flashUsage": "Flash Usage",
  "deviceDetails.field.cpuLoad": "CPU Load",
  "deviceDetails.field.edgeUptime": "Edge Uptime",
  "deviceDetails.field.senderPeriod": "Data Sender Period",
  "deviceDetails.field.healthSource": "Health Source Integration",
  "deviceDetails.value.serialInterface": "/dev/rs485 → /dev/ttyS1",
  "deviceDetails.value.baudRate": "9600 bps, 8N1, No Parity",
  "deviceDetails.value.mpptUnit": "Unit ID: 2 (IPANDEE)",
  "deviceDetails.value.meterUnit": "Unit ID: 1, FC04 (PZEM-6L24)",
  "deviceDetails.value.radiationSensor": "Honde RD-PSR-01 (RS485, slave 3)",
  "deviceDetails.value.senderPeriod": "20 seconds (Lua formatted)",
  "deviceDetails.value.cpuLoadDetail": " · load {load} / {cores} cores",

  // Alarms tab
  "deviceDetails.alarms.active": "Active Alarms",
  "deviceDetails.alarms.events": "Device Event History",
};

export default slice;

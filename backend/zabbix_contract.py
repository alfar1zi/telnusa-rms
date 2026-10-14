"""Single source of truth for approved RUT906 Zabbix trapper items."""
from __future__ import annotations

from dataclasses import dataclass

ZABBIX_SUPPORTED_MAJOR_MINOR = {(7, 4)}
ZABBIX_TRAPPER_TYPE = 2
ZABBIX_VALUE_FLOAT = 0


@dataclass(frozen=True)
class ZabbixItemSpec:
    mqtt_field: str
    zabbix_key: str
    item_type: int
    value_type: int
    units: str
    description: str
    preprocessing: str | None = None


ITEM_SPECS: dict[str, ZabbixItemSpec] = {
    "sps_mppt_pv_v": ZabbixItemSpec("sps_mppt_pv_v", "rut.mppt.pv.v", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "V", "MPPT PV voltage"),
    "sps_mppt_pv_a": ZabbixItemSpec("sps_mppt_pv_a", "rut.mppt.pv.a", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "A", "MPPT PV current"),
    "sps_mppt_out_v": ZabbixItemSpec("sps_mppt_out_v", "rut.mppt.out.v", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "V", "MPPT output voltage"),
    "sps_mppt_out_a": ZabbixItemSpec("sps_mppt_out_a", "rut.mppt.out.a", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "A", "MPPT output current"),
    "a_voltage": ZabbixItemSpec("a_voltage", "rut.pzem.a.voltage", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "V", "PZEM phase A voltage"),
    "b_voltage": ZabbixItemSpec("b_voltage", "rut.pzem.b.voltage", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "V", "PZEM phase B voltage"),
    "c_voltage": ZabbixItemSpec("c_voltage", "rut.pzem.c.voltage", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "V", "PZEM phase C voltage"),
    "a_current": ZabbixItemSpec("a_current", "rut.pzem.a.current", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "A", "PZEM phase A current"),
    "b_current": ZabbixItemSpec("b_current", "rut.pzem.b.current", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "A", "PZEM phase B current"),
    "c_current": ZabbixItemSpec("c_current", "rut.pzem.c.current", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "A", "PZEM phase C current"),
    "kwh": ZabbixItemSpec("kwh", "rut.pzem.energy.kwh", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "kWh", "PZEM cumulative energy"),
    # Solar irradiance (Honde RD-PSR-01). Integrated 2026-10-20 after the
    # physical sensor was field-verified; the router passes the register
    # through 1:1 so the trapped value is already W/m².
    "solar_radiation": ZabbixItemSpec("solar_radiation", "rut.solar.radiation", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "W/m2", "Solar irradiance (Honde RD-PSR-01)"),
    # Router health, sampled on the device from the local RutOS API and carried
    # in the telemetry payload. History lives in Zabbix like every other series.
    "sys_uptime_s": ZabbixItemSpec("sys_uptime_s", "rut.sys.uptime.s", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "s", "Router uptime"),
    "sys_ram_pct": ZabbixItemSpec("sys_ram_pct", "rut.sys.ram.pct", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "%", "Router RAM used"),
    "sys_flash_pct": ZabbixItemSpec("sys_flash_pct", "rut.sys.flash.pct", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "%", "Router flash used"),
    "sys_load1": ZabbixItemSpec("sys_load1", "rut.sys.load1", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "", "Router 1-minute load average"),
    "modem_temperature_c": ZabbixItemSpec("modem_temperature_c", "rut.modem.temperature.c", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "C", "Modem temperature"),
    "modem_signal_dbm": ZabbixItemSpec("modem_signal_dbm", "rut.modem.signal.dbm", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "dBm", "Modem signal strength"),
    "modem_rsrp_dbm": ZabbixItemSpec("modem_rsrp_dbm", "rut.modem.rsrp.dbm", ZABBIX_TRAPPER_TYPE, ZABBIX_VALUE_FLOAT, "dBm", "Modem RSRP"),
}

ZABBIX_KEYS = {raw: spec.zabbix_key for raw, spec in ITEM_SPECS.items()}

def supported_version(version: str) -> bool:
    try:
        major, minor, *_ = [int(part) for part in version.split(".")]
    except ValueError:
        return False
    return (major, minor) in ZABBIX_SUPPORTED_MAJOR_MINOR


def technical_host_name(device_id: str) -> str:
    return device_id

#!/usr/bin/env python3
"""
sensor/inventory.py
Passive Asset Inventory Engine (Step B3).
Builds a live device inventory from passively observed network traffic (IP, MAC, protocol,
role, first seen, last seen, packet count).
Flags unknown/rogue devices immediately with security alerts.
Fulfills Target B.2: "Every device found: The inventory lists all simulated devices, and the rogue device is flagged."
"""

import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Inventory")

# Authorized Asset Baseline for Western Gujarat Solar Park (ICR-04)
DEFAULT_BASELINE = {
    "192.168.10.11": {
        "name": "Solar Inverter 01 (Central 2.5MW)",
        "mac": "00:1B:C5:01:00:11",
        "vendor": "Sungrow / TMEIC",
        "role": "Solar PV Inverter",
        "purdue_level": "Level 1 (Field Controller)",
        "allowed_protocols": ["Modbus TCP"],
        "is_authorized": True,
    },
    "192.168.10.12": {
        "name": "Solar Inverter 02 (Central 2.5MW)",
        "mac": "00:1B:C5:01:00:12",
        "vendor": "Sungrow / TMEIC",
        "role": "Solar PV Inverter",
        "purdue_level": "Level 1 (Field Controller)",
        "allowed_protocols": ["Modbus TCP"],
        "is_authorized": True,
    },
    "192.168.10.13": {
        "name": "Solar Inverter 03 (Central 2.5MW)",
        "mac": "00:1B:C5:01:00:13",
        "vendor": "Sungrow / TMEIC",
        "role": "Solar PV Inverter",
        "purdue_level": "Level 1 (Field Controller)",
        "allowed_protocols": ["Modbus TCP"],
        "is_authorized": True,
    },
    "192.168.10.20": {
        "name": "Substation RTU / Field PLC",
        "mac": "00:80:F4:02:00:20",
        "vendor": "ABB / Siemens",
        "role": "Substation RTU/PLC",
        "purdue_level": "Level 1/2 (Supervisory)",
        "allowed_protocols": ["Modbus TCP"],
        "is_authorized": True,
    },
    "192.168.10.5": {
        "name": "ICR Plant SCADA Master HMI",
        "mac": "00:50:56:A0:00:05",
        "vendor": "Dell Industrial PC",
        "role": "Plant SCADA Master",
        "purdue_level": "Level 2 (Control Room)",
        "allowed_protocols": ["Modbus TCP"],
        "is_authorized": True,
    },
    "192.168.10.30": {
        "name": "Substation Telecontrol Gateway",
        "mac": "00:80:F4:03:00:30",
        "vendor": "Moxa / Cisco Industrial",
        "role": "IEC-104 Substation Gateway",
        "purdue_level": "Level 3 (Operations DMZ)",
        "allowed_protocols": ["IEC 60870-5-104"],
        "is_authorized": True,
    },
    "10.100.0.10": {
        "name": "State Load Despatch Centre (SLDC / EMS)",
        "mac": "00:0C:29:FE:00:10",
        "vendor": "Gujarat Energy Transmission Corp (GETCO)",
        "role": "Grid Dispatch Master",
        "purdue_level": "Level 4 (Enterprise/Grid)",
        "allowed_protocols": ["IEC 60870-5-104"],
        "is_authorized": True,
    },
}

class DeviceRecord:
    def __init__(self,
                 ip: str,
                 mac: str,
                 name: str,
                 vendor: str,
                 role: str,
                 purdue_level: str,
                 is_authorized: bool):
        self.ip = ip
        self.mac = mac
        self.name = name
        self.vendor = vendor
        self.role = role
        self.purdue_level = purdue_level
        self.is_authorized = is_authorized
        self.first_seen = time.time()
        self.last_seen = time.time()
        self.protocols = set()
        self.packet_count = 0
        self.bytes_transferred = 0
        self.alerts_count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "mac": self.mac,
            "name": self.name,
            "vendor": self.vendor,
            "role": self.role,
            "purdue_level": self.purdue_level,
            "is_authorized": self.is_authorized,
            "status": "AUTHORIZED" if self.is_authorized else "ROGUE_DEVICE",
            "first_seen": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.first_seen)),
            "last_seen": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_seen)),
            "last_seen_epoch": self.last_seen,
            "protocols": sorted(list(self.protocols)),
            "packet_count": self.packet_count,
            "bytes_transferred": self.bytes_transferred,
            "alerts_count": self.alerts_count,
        }

class DeviceInventory:
    def __init__(self, baseline: Optional[Dict[str, Any]] = None):
        self.baseline = baseline or DEFAULT_BASELINE
        self.devices: Dict[str, DeviceRecord] = {}
        self.new_device_callbacks = []
        self._initialize_baseline_devices()

    def _initialize_baseline_devices(self):
        """Pre-populate inventory with known authorized assets."""
        for ip, info in self.baseline.items():
            record = DeviceRecord(
                ip=ip,
                mac=info["mac"],
                name=info["name"],
                vendor=info["vendor"],
                role=info["role"],
                purdue_level=info["purdue_level"],
                is_authorized=True,
            )
            for p in info.get("allowed_protocols", []):
                record.protocols.add(p)
            self.devices[ip] = record

    def register_new_device_callback(self, callback):
        self.new_device_callbacks.append(callback)

    def observe(self, ip: str, mac: Optional[str] = None, protocol: Optional[str] = None, byte_len: int = 0) -> Optional[Dict[str, Any]]:
        """
        Observes a device on the wire passively.
        If unknown, generates a security alert and flags as ROGUE.
        Returns alert dict if a new rogue device was detected, else None.
        """
        now = time.time()
        is_new = False
        alert = None

        if ip not in self.devices:
            is_new = True
            is_auth = ip in self.baseline
            base_info = self.baseline.get(ip, {})
            name = base_info.get("name", "Unknown Ethernet Host")
            vendor = base_info.get("vendor", "Unrecognized Hardware")
            role = base_info.get("role", "Rogue / Unclassified Device")
            purdue = base_info.get("purdue_level", "Unknown Level (Violation)")
            
            record = DeviceRecord(
                ip=ip,
                mac=mac or "AA:BB:CC:DD:EE:FF",
                name=name,
                vendor=vendor,
                role=role,
                purdue_level=purdue,
                is_authorized=is_auth,
            )
            self.devices[ip] = record
            
            if not is_auth:
                alert = {
                    "alert_id": f"ALT-ROGUE-{int(now * 1000) % 1000000}",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                    "epoch": now,
                    "severity": "CRITICAL",
                    "category": "INVENTORY_VIOLATION",
                    "source_ip": ip,
                    "target_ip": "BROADCAST / ICR SWITCH",
                    "protocol": protocol or "Ethernet/IP",
                    "title": "Rogue Device Plugged into ICR Switch (CEA / IEC 62443 Violation)",
                    "reason": f"Unregistered MAC {record.mac} with IP {ip} was detected transmitting on the Inverter Control Room internal switch without authorization.",
                    "mitre_technique": "T0886 - Remote System Discovery / T0843 - Program Download",
                    "action_required": "Initiate CEA Playbook: isolate switch port immediately, verify physical perimeter lock, dispatch field technician.",
                }
                record.alerts_count += 1
                logger.critical(f"ROGUE DEVICE DETECTED: IP={ip} MAC={record.mac} -> ALERT RAISED!")
                for cb in self.new_device_callbacks:
                    try:
                        cb(alert)
                    except Exception:
                        pass
        else:
            record = self.devices[ip]

        record.last_seen = now
        record.packet_count += 1
        record.bytes_transferred += byte_len
        if protocol:
            record.protocols.add(protocol)
        if mac and record.mac == "AA:BB:CC:DD:EE:FF":
            record.mac = mac

        return alert

    def get_all_devices(self) -> List[Dict[str, Any]]:
        return [dev.to_dict() for dev in self.devices.values()]

    def get_device(self, ip: str) -> Optional[Dict[str, Any]]:
        dev = self.devices.get(ip)
        return dev.to_dict() if dev else None

    def get_summary(self) -> Dict[str, Any]:
        total = len(self.devices)
        auth = sum(1 for d in self.devices.values() if d.is_authorized)
        rogue = total - auth
        return {
            "total_devices": total,
            "authorized_devices": auth,
            "rogue_devices": rogue,
            "healthy": rogue == 0,
        }

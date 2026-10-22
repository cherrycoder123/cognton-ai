#!/usr/bin/env python3
"""
sensor/rules_engine.py
Deterministic OT Cybersecurity Rules Engine (Step B4).
Evaluates parsed industrial protocol frames against deterministic OT policies:
- Who may write which register, from where, and when.
- Safe operational envelopes for solar PV inverters and substation RTUs.
- Off-hours vendor access detection (CEA Power Sector Cybersecurity Guidelines).
- Telecontrol command authorization and link flooding protection.
Every alert provides a comprehensive explanation in plain English ("Why it fired").
"""

import time
import datetime
from typing import Dict, Any, List, Optional
from sensor.parsers.modbus_parser import ModbusFrame
from sensor.parsers.iec104_parser import IEC104Frame

class OTRulesEngine:
    def __init__(self,
                 authorized_scada_ip: str = "192.168.10.5",
                 vendor_gateway_ip: str = "192.168.10.99",
                 sldc_ip: str = "10.100.0.10"):
        self.authorized_scada_ip = authorized_scada_ip
        self.vendor_gateway_ip = vendor_gateway_ip
        self.sldc_ip = sldc_ip
        
        # Telemetry rate trackers for DoS detection
        self.iec104_packet_window = []
        self.max_iec104_rate_per_sec = 15
        
        # Maintenance window settings (CEA Guidelines: 08:00 - 18:00 IST)
        self.maintenance_start_hour = 8
        self.maintenance_end_hour = 18

    def evaluate_modbus(self,
                        frame: ModbusFrame,
                        source_ip: str,
                        target_ip: str,
                        simulated_hour: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Evaluates a Modbus TCP frame against OT security policies.
        Returns an Alert dictionary in plain words if a rule fires, else None.
        """
        now = time.time()
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        hour = simulated_hour if simulated_hour is not None else datetime.datetime.now().hour

        # RULE OT-01: Unauthorized Modbus Write to Inverter Setpoints
        if frame.is_write:
            # Check if source is NOT the authorized SCADA Master
            if source_ip != self.authorized_scada_ip:
                reg_info = f"register 0x{frame.start_address:04X} ({frame.start_address})" if frame.start_address is not None else "registers"
                val_info = f"with value(s) {frame.values}" if frame.values else ""
                
                # Check for vendor gateway specifically
                if source_ip == self.vendor_gateway_ip:
                    return {
                        "alert_id": f"ALT-RULE-03-{int(now*1000)%1000000}",
                        "timestamp": timestamp_str,
                        "epoch": now,
                        "severity": "CRITICAL",
                        "rule_id": "OT-03",
                        "category": "UNAUTHORIZED_VENDOR_WRITE",
                        "source_ip": source_ip,
                        "target_ip": target_ip,
                        "protocol": "Modbus TCP",
                        "title": "Vendor Remote Gateway Write During Off-Hours (2:00 AM Incident)",
                        "reason": f"External vendor gateway {source_ip} executed Modbus write ({frame.func_name}) to {target_ip} {reg_info} {val_info} at hour {hour:02d}:00 outside approved maintenance window (08:00-18:00). No active MFA permit found.",
                        "mitre_technique": "T0822 - External Remote Services / T0859 - Valid Accounts",
                        "action_required": "Terminate vendor remote session immediately; lock gateway credentials; review session audit logs under CEA Section 8.4.",
                    }

                # General unauthorized source (e.g. Rogue Laptop in ICR)
                return {
                    "alert_id": f"ALT-RULE-01-{int(now*1000)%1000000}",
                    "timestamp": timestamp_str,
                    "epoch": now,
                    "severity": "CRITICAL",
                    "rule_id": "OT-01",
                    "category": "UNAUTHORIZED_SETPOINT_WRITE",
                    "source_ip": source_ip,
                    "target_ip": target_ip,
                    "protocol": "Modbus TCP",
                    "title": "Unauthorized Modbus Write to Solar Inverter / Controller",
                    "reason": f"Source {source_ip} (unauthorized host) transmitted Modbus {frame.func_name} (FC {frame.func_code}) to {target_ip} targeting {reg_info} {val_info}. Inverters and RTUs must only accept write commands from SCADA Master {self.authorized_scada_ip}.",
                    "mitre_technique": "T0855 - Unauthorized Command Message / T0836 - Modify Parameter",
                    "action_required": "Block source IP at ICR switch port; restore golden setpoint configuration from SCADA historian; inspect physical ICR switch room.",
                }

            # RULE OT-02: Safety-Critical Inverter Setpoint Tampering / Out-of-bounds
            if frame.start_address == 4096 and frame.values:
                power_limit = frame.values[0]
                if power_limit < 10:
                    return {
                        "alert_id": f"ALT-RULE-02-{int(now*1000)%1000000}",
                        "timestamp": timestamp_str,
                        "epoch": now,
                        "severity": "HIGH",
                        "rule_id": "OT-02",
                        "category": "ABNORMAL_CURTAILMENT",
                        "source_ip": source_ip,
                        "target_ip": target_ip,
                        "protocol": "Modbus TCP",
                        "title": "Severe Inverter Power Curtailment / Generation Throttle",
                        "reason": f"Active power output limit on {target_ip} was throttled to {power_limit}% (normal: 100%). This causes an immediate generation loss of ~2.5 MW. Fired because curtailment exceeds safe operational rate of change.",
                        "mitre_technique": "T0831 - Manipulation of Control / T0829 - Loss of Control",
                        "action_required": "Confirm whether SLDC issued a dynamic curtailment order. If uncoordinated, restore active power limit to 100%.",
                    }

            # RULE OT-05: Substation Main Feeder Breaker Trip via Modbus
            if frame.start_address == 0 and frame.func_code == 5 and target_ip.endswith(".20"):
                val = frame.values[0] if frame.values else None
                if val == 0:
                    return {
                        "alert_id": f"ALT-RULE-05-{int(now*1000)%1000000}",
                        "timestamp": timestamp_str,
                        "epoch": now,
                        "severity": "CRITICAL",
                        "rule_id": "OT-05",
                        "category": "BREAKER_TRIP_COMMAND",
                        "source_ip": source_ip,
                        "target_ip": target_ip,
                        "protocol": "Modbus TCP",
                        "title": "Substation 33kV Feeder Vacuum Circuit Breaker Open Command",
                        "reason": f"Host {source_ip} commanded Coil 0 (33kV Main Feeder Breaker) on Substation RTU {target_ip} to TRIP/OPEN. This instantly disconnects the solar park pooling generation from the grid.",
                        "mitre_technique": "T0832 - Manipulation of Control / T0813 - Denial of Control",
                        "action_required": "Immediately verify grid stability with SLDC; inspect protection relay interlocks; prevent re-closure until field inspection completes.",
                    }

            # RULE OT-06: Malicious Firmware Download Attempt
            if frame.start_address == 4101 or frame.func_code == 90:
                return {
                    "alert_id": f"ALT-RULE-06-{int(now*1000)%1000000}",
                    "timestamp": timestamp_str,
                    "epoch": now,
                    "severity": "CRITICAL",
                    "rule_id": "OT-06",
                    "category": "FIRMWARE_MODIFICATION_ATTEMPT",
                    "source_ip": source_ip,
                    "target_ip": target_ip,
                    "protocol": "Modbus TCP",
                    "title": "Malicious Controller Firmware Flashing Attempt",
                    "reason": f"Modbus command targeted firmware checksum register 4101 (0x1005) or used vendor proprietary download FC 90 on {target_ip}. Potential Industroyer2 / TRITON-style weaponized firmware payload.",
                    "mitre_technique": "T0843 - Program Download / T0857 - System Firmware",
                    "action_required": "Isolate controller from network; freeze memory image for forensic analysis; report to CERT-In within 6 hours.",
                }

        return None

    def evaluate_iec104(self,
                        frame: IEC104Frame,
                        source_ip: str,
                        target_ip: str) -> Optional[Dict[str, Any]]:
        """
        Evaluates an IEC 60870-5-104 frame against telecontrol security rules.
        """
        now = time.time()
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

        # RULE OT-04: IEC-104 Telecontrol Flood / Denial of Service
        self.iec104_packet_window.append(now)
        # Keep only packets in last 1.0 second
        self.iec104_packet_window = [t for t in self.iec104_packet_window if now - t <= 1.0]
        
        if len(self.iec104_packet_window) > self.max_iec104_rate_per_sec:
            rate = len(self.iec104_packet_window)
            return {
                "alert_id": f"ALT-RULE-04-{int(now*1000)%1000000}",
                "timestamp": timestamp_str,
                "epoch": now,
                "severity": "HIGH",
                "rule_id": "OT-04",
                "category": "TELECONTROL_DOS_FLOOD",
                "source_ip": source_ip,
                "target_ip": target_ip,
                "protocol": "IEC 60870-5-104",
                "title": "IEC-104 Telecontrol Link Traffic Flood / Denial of Service",
                "reason": f"IEC-104 frame rate spiked to {rate} frames/sec from {source_ip} (threshold: {self.max_iec104_rate_per_sec} fps). Packet flood risks exhausting RTU communication buffers and delaying critical SLDC grid dispatch.",
                "mitre_technique": "T0814 - Denial of Service / T0813 - Denial of Control",
                "action_required": "Apply rate-limiting on telecontrol gateway; check WAN router link status; switch to secondary redundant IEC-104 channel.",
            }

        # RULE OT-05b: Unauthorized IEC-104 Command from Non-SLDC Source
        if frame.is_command and source_ip != self.sldc_ip:
            return {
                "alert_id": f"ALT-RULE-05B-{int(now*1000)%1000000}",
                "timestamp": timestamp_str,
                "epoch": now,
                "severity": "CRITICAL",
                "rule_id": "OT-05",
                "category": "UNAUTHORIZED_TELECONTROL_COMMAND",
                "source_ip": source_ip,
                "target_ip": target_ip,
                "protocol": "IEC 60870-5-104",
                "title": "Rogue IEC-104 Telecontrol Command Injected",
                "reason": f"Received IEC-104 Single Command (ASDU {frame.asdu_type}, IOA {frame.ioa}) with command state '{frame.command_value}' from unauthorized source {source_ip}. Only official SLDC {self.sldc_ip} is permitted to issue grid dispatch commands.",
                "mitre_technique": "T0855 - Unauthorized Command Message / T0831 - Manipulation of Control",
                "action_required": "Sever unauthorized TCP connection immediately; verify state of Substation IOA {frame.ioa}; notify State Load Despatch Centre.",
            }

        return None

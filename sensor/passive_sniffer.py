#!/usr/bin/env python3
"""
sensor/passive_sniffer.py
Core Passive Security Watchman Sensor (Step B2, B3, B4).
Passively monitors mirrored industrial network traffic (TAP/SPAN port).
Zero-packet-injection architecture ensures zero interference with control traffic.
Orchestrates:
- Live Device Inventory (inventory.py)
- Protocol Dissectors (Modbus & IEC-104)
- Deterministic OT Rule Engine (rules_engine.py)
- Machine Learning Anomaly Detector (ml_engine.py)
"""

import socket
import struct
import threading
import time
import logging
from typing import Dict, Any, List, Optional, Callable

from sensor.parsers.modbus_parser import ModbusParser
from sensor.parsers.iec104_parser import IEC104Parser
from sensor.inventory import DeviceInventory
from sensor.rules_engine import OTRulesEngine
from sensor.ml_engine import SolarAnomalyDetector

logger = logging.getLogger("PassiveWatchman")

class AlertStore:
    def __init__(self, max_alerts: int = 1000):
        self.alerts = []
        self.max_alerts = max_alerts
        self.lock = threading.Lock()
        self.listeners = []

    def add(self, alert: Dict[str, Any]):
        with self.lock:
            self.alerts.insert(0, alert)
            if len(self.alerts) > self.max_alerts:
                self.alerts.pop()
        for listener in self.listeners:
            try:
                listener(alert)
            except Exception:
                pass

    def get_all(self) -> List[Dict[str, Any]]:
        with self.lock:
            return list(self.alerts)

    def register_listener(self, callback: Callable[[Dict[str, Any]], None]):
        self.listeners.append(callback)

class PassiveSecurityWatchman:
    def __init__(self,
                 inventory: Optional[DeviceInventory] = None,
                 rules_engine: Optional[OTRulesEngine] = None,
                 ml_engine: Optional[SolarAnomalyDetector] = None):
        self.inventory = inventory or DeviceInventory()
        self.rules = rules_engine or OTRulesEngine()
        self.ml = ml_engine or SolarAnomalyDetector()
        self.alert_store = AlertStore()
        self.running = False
        
        # Link inventory rogue alerts into alert store
        self.inventory.register_new_device_callback(self._on_inventory_alert)
        
        # Traffic statistics
        self.stats = {
            "packets_analyzed": 0,
            "modbus_frames": 0,
            "iec104_frames": 0,
            "alerts_generated": 0,
            "start_time": time.time(),
            "bytes_processed": 0,
        }
        self.lock = threading.Lock()

    def _on_inventory_alert(self, alert: Dict[str, Any]):
        self.alert_store.add(alert)
        with self.lock:
            self.stats["alerts_generated"] += 1

    def process_packet(self,
                       raw_payload: bytes,
                       source_ip: str,
                       target_ip: str,
                       dest_port: int,
                       source_mac: Optional[str] = None,
                       simulated_hour: Optional[int] = None):
        """
        Processes an observed network packet through the passive inspection pipeline.
        This is called by the TAP listener or simulated packet pipeline.
        """
        now = time.time()
        byte_len = len(raw_payload)
        
        with self.lock:
            self.stats["packets_analyzed"] += 1
            self.stats["bytes_processed"] += byte_len

        # 1. Update Asset Inventory
        protocol_tag = "Modbus TCP" if dest_port in (502, 5021, 5022, 5023, 5024) else ("IEC 60870-5-104" if dest_port == 2404 else "TCP/IP")
        rogue_alert = self.inventory.observe(source_ip, mac=source_mac, protocol=protocol_tag, byte_len=byte_len)
        self.inventory.observe(target_ip, byte_len=byte_len)

        # 2. Modbus TCP Protocol Inspection
        if dest_port in (502, 5021, 5022, 5023, 5024) or (len(raw_payload) >= 7 and raw_payload[2:4] == b"\x00\x00"):
            frame = ModbusParser.parse(raw_payload, is_response=False)
            if frame:
                with self.lock:
                    self.stats["modbus_frames"] += 1
                
                # Rule Evaluation
                alert = self.rules.evaluate_modbus(frame, source_ip, target_ip, simulated_hour=simulated_hour)
                if alert:
                    self.alert_store.add(alert)
                    with self.lock:
                        self.stats["alerts_generated"] += 1

        # 3. IEC 60870-5-104 Protocol Inspection
        elif dest_port == 2404 or (len(raw_payload) >= 6 and raw_payload[0] == 0x68):
            iec_frame = IEC104Parser.parse(raw_payload)
            if iec_frame:
                with self.lock:
                    self.stats["iec104_frames"] += 1
                
                alert = self.rules.evaluate_iec104(iec_frame, source_ip, target_ip)
                if alert:
                    self.alert_store.add(alert)
                    with self.lock:
                        self.stats["alerts_generated"] += 1

    def process_telemetry_vector(self,
                                 device_id: str,
                                 ac_power_kw: float,
                                 dc_voltage: float,
                                 irradiance: float,
                                 heat_sink_temp: float,
                                 packet_rate_pps: float,
                                 write_frequency: float):
        """Passes operational metrics into the ML anomaly engine."""
        ml_alert = self.ml.evaluate(
            device_id=device_id,
            ac_power_kw=ac_power_kw,
            dc_voltage=dc_voltage,
            irradiance=irradiance,
            heat_sink_temp=heat_sink_temp,
            packet_rate_pps=packet_rate_pps,
            write_frequency=write_frequency
        )
        if ml_alert:
            self.alert_store.add(ml_alert)
            with self.lock:
                self.stats["alerts_generated"] += 1

    def get_summary(self) -> Dict[str, Any]:
        with self.lock:
            uptime_sec = time.time() - self.stats["start_time"]
            inv_sum = self.inventory.get_summary()
            return {
                "uptime_seconds": round(uptime_sec, 1),
                "packets_analyzed": self.stats["packets_analyzed"],
                "bytes_processed_kb": round(self.stats["bytes_processed"] / 1024.0, 2),
                "modbus_frames": self.stats["modbus_frames"],
                "iec104_frames": self.stats["iec104_frames"],
                "alerts_total": self.stats["alerts_generated"],
                "total_devices": inv_sum["total_devices"],
                "rogue_devices": inv_sum["rogue_devices"],
                "active_alerts": len(self.alert_store.get_all()),
            }

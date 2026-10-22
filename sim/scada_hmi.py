#!/usr/bin/env python3
"""
sim/scada_hmi.py
Simulates the Inverter Control Room (ICR) Plant SCADA Master / HMI (Purdue Level 2).
Periodically polls Inverter-01, Inverter-02, Inverter-03 and the RTU/PLC via Modbus TCP.
Tracks round-trip latency, polling success rate, and active telemetry.
Provides evidence for Target B.2 #5: "No impact on control traffic (SCADA polling keeps working)".
"""

import socket
import struct
import threading
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [SCADA_HMI] %(message)s")
logger = logging.getLogger("SCADA_HMI")

class SCADAMasterPollingService:
    def __init__(self, virtual_ip: str = "192.168.10.5"):
        self.virtual_ip = virtual_ip
        self.running = False
        self.poll_interval = 1.5 # seconds
        self.targets = [
            {"id": "INV-01", "name": "Solar Inverter 01", "host": "127.0.0.1", "port": 5021, "virtual_ip": "192.168.10.11"},
            {"id": "INV-02", "name": "Solar Inverter 02", "host": "127.0.0.1", "port": 5022, "virtual_ip": "192.168.10.12"},
            {"id": "INV-03", "name": "Solar Inverter 03", "host": "127.0.0.1", "port": 5023, "virtual_ip": "192.168.10.13"},
            {"id": "RTU-01", "name": "Substation RTU/PLC", "host": "127.0.0.1", "port": 5024, "virtual_ip": "192.168.10.20"},
        ]
        
        # Telemetry Store
        self.telemetry = {
            "INV-01": {"ac_power_kw": 0, "dc_voltage": 0, "status": "UNKNOWN", "latency_ms": 0},
            "INV-02": {"ac_power_kw": 0, "dc_voltage": 0, "status": "UNKNOWN", "latency_ms": 0},
            "INV-03": {"ac_power_kw": 0, "dc_voltage": 0, "status": "UNKNOWN", "latency_ms": 0},
            "RTU-01": {"grid_export_kw": 0, "irradiance": 0, "breaker": "CLOSED", "latency_ms": 0},
        }
        
        # Polling statistics for Target B.2 verification
        self.stats = {
            "total_polls": 0,
            "successful_polls": 0,
            "failed_polls": 0,
            "avg_latency_ms": 0.0,
            "min_latency_ms": 999.0,
            "max_latency_ms": 0.0,
            "sensor_active": False,
        }
        self.lock = threading.Lock()
        self.worker_thread = None

    def start(self):
        self.running = True
        self.worker_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.worker_thread.start()
        logger.info(f"SCADA Master Polling Service started (Virtual IP: {self.virtual_ip})")

    def _polling_loop(self):
        trans_id = 0
        while self.running:
            start_cycle = time.time()
            for target in self.targets:
                trans_id = (trans_id + 1) % 65535
                success, latency = self._poll_target(target, trans_id)
                with self.lock:
                    self.stats["total_polls"] += 1
                    if success:
                        self.stats["successful_polls"] += 1
                        prev_avg = self.stats["avg_latency_ms"]
                        n = self.stats["successful_polls"]
                        self.stats["avg_latency_ms"] = round(prev_avg + (latency - prev_avg) / n, 2)
                        if latency < self.stats["min_latency_ms"]:
                            self.stats["min_latency_ms"] = round(latency, 2)
                        if latency > self.stats["max_latency_ms"]:
                            self.stats["max_latency_ms"] = round(latency, 2)
                    else:
                        self.stats["failed_polls"] += 1
            elapsed = time.time() - start_cycle
            sleep_time = max(0.1, self.poll_interval - elapsed)
            time.sleep(sleep_time)

    def _poll_target(self, target: dict, trans_id: int):
        s = None
        t0 = time.time()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((target["host"], target["port"]))
            
            # Read Input Registers FC 04 (Start 0, Count 6)
            # MBAP: TransID (2B), Proto 0 (2B), Len 6 (2B), Unit 1 (1B)
            # PDU: FC 04 (1B), Start 0 (2B), Count 6 (2B)
            mbap = struct.pack(">HHHB", trans_id, 0, 6, 1)
            pdu = struct.pack(">BHH", 4, 0, 6)
            s.sendall(mbap + pdu)
            
            resp_hdr = s.recv(7)
            if len(resp_hdr) < 7:
                return False, 0
            _, _, length, _ = struct.unpack(">HHHB", resp_hdr)
            pdu_len = length - 1
            resp_pdu = s.recv(pdu_len)
            latency_ms = (time.time() - t0) * 1000.0
            
            if resp_pdu and resp_pdu[0] == 4:
                vals = []
                byte_count = resp_pdu[1]
                for i in range(byte_count // 2):
                    v = struct.unpack(">H", resp_pdu[2 + i*2 : 4 + i*2])[0]
                    vals.append(v)
                
                with self.lock:
                    tid = target["id"]
                    if tid.startswith("INV"):
                        self.telemetry[tid]["dc_voltage"] = vals[0]
                        self.telemetry[tid]["ac_power_kw"] = vals[3]
                        self.telemetry[tid]["status"] = "NORMAL"
                        self.telemetry[tid]["latency_ms"] = round(latency_ms, 2)
                    elif tid == "RTU-01":
                        self.telemetry[tid]["irradiance"] = vals[0]
                        self.telemetry[tid]["grid_export_kw"] = vals[5]
                        self.telemetry[tid]["latency_ms"] = round(latency_ms, 2)
                return True, latency_ms
            return False, latency_ms
        except Exception:
            with self.lock:
                self.telemetry[target["id"]]["status"] = "COMM_FAIL"
            return False, (time.time() - t0) * 1000.0
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def get_summary(self):
        with self.lock:
            total_solar_kw = sum(v.get("ac_power_kw", 0) for k, v in self.telemetry.items() if k.startswith("INV"))
            total = self.stats["total_polls"]
            succ = self.stats["successful_polls"]
            reliability = (succ / total * 100.0) if total > 0 else 100.0
            return {
                "total_solar_kw": total_solar_kw,
                "total_solar_mw": round(total_solar_kw / 1000.0, 2),
                "reliability_pct": round(reliability, 2),
                "avg_latency_ms": self.stats["avg_latency_ms"],
                "total_polls": self.stats["total_polls"],
                "failed_polls": self.stats["failed_polls"],
                "devices": self.telemetry
            }

    def stop(self):
        self.running = False

if __name__ == "__main__":
    scada = SCADAMasterPollingService()
    scada.start()
    try:
        while True:
            time.sleep(2)
            summary = scada.get_summary()
            logger.info(f"SCADA Telemetry: Total Power = {summary['total_solar_mw']} MW | Reliability = {summary['reliability_pct']}% | Avg Latency = {summary['avg_latency_ms']} ms")
    except KeyboardInterrupt:
        scada.stop()

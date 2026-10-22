#!/usr/bin/env python3
"""
sim/inverters.py
Simulates 3 Utility-Scale Solar PV Inverters (Purdue Level 1) running Modbus TCP servers.
Each inverter simulates realistic solar physics (irradiance, DC-to-AC conversion,
heat sink temperature, active/reactive power, and setpoint registers).
"""

import socket
import struct
import threading
import time
import math
import random
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [InverterSim] %(message)s")
logger = logging.getLogger("Inverters")

class SolarInverterServer:
    def __init__(self, inverter_id: int, host: str, port: int, virtual_ip: str, rated_capacity_kw: float = 2500.0):
        self.inverter_id = inverter_id
        self.host = host
        self.port = port
        self.virtual_ip = virtual_ip
        self.rated_capacity_kw = rated_capacity_kw
        self.running = False
        self.sock = None
        self.clients = []
        
        # Modbus Holding Registers (Function Codes 03 / 06 / 16)
        # Register 0x1000 (4096): Active Power Setpoint (0-100%, default 100)
        # Register 0x1001 (4097): Reactive Power Setpoint (kVAR, default 0)
        # Register 0x1002 (4098): Power Factor Setpoint * 1000 (default 1000 = 1.0)
        # Register 0x1003 (4099): Inverter Operating Mode (1=Run, 2=Curtail, 3=Standby, 4=Trip)
        # Register 0x1004 (4100): Emergency Stop / Breaker Trip (0=Closed, 1=Tripped)
        # Register 0x1005 (4101): Firmware Hash Register (Build 0x7A42)
        self.holding_registers = {
            4096: 100,      # 100% active power allowed
            4097: 0,        # 0 kVAR
            4098: 1000,     # PF 1.000
            4099: 1,        # Mode 1: Normal Run
            4100: 0,        # Emergency Stop: 0 (Normal)
            4101: 0x7A42,   # Authorized Firmware Checksum
        }
        
        # Modbus Input Registers (Function Code 04)
        # 30001: DC Voltage (V, e.g. 1100 V)
        # 30002: DC Current (A, e.g. 2270 A)
        # 30003: DC Power (kW)
        # 30004: AC Active Power (kW)
        # 30005: AC Frequency (Hz * 100, e.g. 5000 = 50.00 Hz)
        # 30006: Heat Sink Temperature (deg C)
        # 30007: Daily Energy Yield (kWh)
        self.input_registers = {
            0: 1100,
            1: 2270,
            2: 2497,
            3: 2450,
            4: 5000,
            5: 48,
            6: 18500,
        }
        
        self.lock = threading.Lock()
        self.physics_thread = None

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(10)
        
        self.physics_thread = threading.Thread(target=self._simulate_physics, daemon=True)
        self.physics_thread.start()
        
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()
        logger.info(f"Inverter-{self.inverter_id:02d} listening on {self.host}:{self.port} (Virtual IP: {self.virtual_ip})")

    def _simulate_physics(self):
        """Simulate realistic solar PV physics variations and ambient effects."""
        tick = 0
        while self.running:
            tick += 1
            time.sleep(1.0)
            with self.lock:
                power_limit_pct = self.holding_registers.get(4096, 100)
                is_tripped = self.holding_registers.get(4100, 0) == 1
                mode = self.holding_registers.get(4099, 1)

                if is_tripped or mode == 4:
                    ac_power = 0
                    dc_voltage = 1250 # Open circuit voltage Voc
                    dc_current = 0
                    dc_power = 0
                    heat_sink_temp = max(38, self.input_registers[5] - 0.2)
                else:
                    # Diurnal curve simulation with small solar noise
                    irradiance = 850.0 + 35.0 * math.sin(tick * 0.05) + random.uniform(-10.0, 10.0)
                    available_kw = (irradiance / 1000.0) * self.rated_capacity_kw
                    
                    # Throttled by holding register set-point
                    target_kw = available_kw * (power_limit_pct / 100.0)
                    efficiency = 0.985
                    ac_power = target_kw * efficiency
                    dc_voltage = 1100.0 + random.uniform(-5.0, 5.0)
                    dc_current = (target_kw * 1000.0) / dc_voltage if dc_voltage > 0 else 0
                    dc_power = target_kw
                    heat_sink_temp = 45.0 + (ac_power / self.rated_capacity_kw) * 20.0 + random.uniform(-0.5, 0.5)

                freq_hz_scaled = int(5000 + random.uniform(-5, 5)) # 50.00 Hz nominal

                self.input_registers[0] = int(dc_voltage)
                self.input_registers[1] = int(dc_current)
                self.input_registers[2] = int(dc_power)
                self.input_registers[3] = int(ac_power)
                self.input_registers[4] = freq_hz_scaled
                self.input_registers[5] = int(heat_sink_temp)
                self.input_registers[6] = self.input_registers.get(6, 18000) + int(ac_power / 3600.0)

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
                self.clients.append(conn)
                t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()
            except Exception:
                break

    def _handle_client(self, conn, addr):
        while self.running:
            try:
                # Modbus TCP MBAP Header = 7 bytes:
                # Transaction ID (2B), Protocol ID (2B, 0 for Modbus), Length (2B), Unit ID (1B)
                header = conn.recv(7)
                if not header or len(header) < 7:
                    break
                trans_id, proto_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu_len = length - 1
                pdu = b""
                while len(pdu) < pdu_len:
                    chunk = conn.recv(pdu_len - len(pdu))
                    if not chunk:
                        break
                    pdu += chunk

                if len(pdu) < pdu_len:
                    break

                response_pdu = self._process_modbus_pdu(pdu)
                if response_pdu:
                    resp_mbap = struct.pack(">HHHB", trans_id, proto_id, len(response_pdu) + 1, unit_id)
                    conn.sendall(resp_mbap + response_pdu)
            except Exception:
                break
        try:
            conn.close()
            if conn in self.clients:
                self.clients.remove(conn)
        except Exception:
            pass

    def _process_modbus_pdu(self, pdu: bytes) -> bytes:
        if not pdu:
            return b""
        fc = pdu[0]
        
        # FC 03: Read Holding Registers
        if fc == 3:
            start_addr, count = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                byte_count = count * 2
                vals = []
                for addr in range(start_addr, start_addr + count):
                    vals.append(self.holding_registers.get(addr, 0))
                resp = struct.pack(">BB", fc, byte_count)
                for v in vals:
                    resp += struct.pack(">H", v & 0xFFFF)
                return resp

        # FC 04: Read Input Registers
        elif fc == 4:
            start_addr, count = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                byte_count = count * 2
                vals = []
                for addr in range(start_addr, start_addr + count):
                    vals.append(self.input_registers.get(addr, 0))
                resp = struct.pack(">BB", fc, byte_count)
                for v in vals:
                    resp += struct.pack(">H", v & 0xFFFF)
                return resp

        # FC 06: Write Single Register
        elif fc == 6:
            reg_addr, val = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                self.holding_registers[reg_addr] = val
                logger.warning(f"[Inverter-{self.inverter_id:02d}] Modbus WRITE to reg 0x{reg_addr:04X} ({reg_addr}) = {val}")
                return pdu # Echo standard response

        # FC 16 (0x10): Write Multiple Registers
        elif fc == 16:
            start_addr, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            vals = []
            for i in range(count):
                val = struct.unpack(">H", pdu[6 + i*2 : 8 + i*2])[0]
                vals.append(val)
            with self.lock:
                for idx, v in enumerate(vals):
                    self.holding_registers[start_addr + idx] = v
                logger.warning(f"[Inverter-{self.inverter_id:02d}] Modbus WRITE_MULTI to reg 0x{start_addr:04X} count={count}")
                return struct.pack(">BHH", fc, start_addr, count)

        # Unsupported or Exception
        else:
            return struct.pack(">BB", fc | 0x80, 0x01) # Illegal Function

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


class InverterCluster:
    def __init__(self, base_port: int = 5021):
        self.inverters = [
            SolarInverterServer(inverter_id=1, host="127.0.0.1", port=base_port, virtual_ip="192.168.10.11", rated_capacity_kw=2500.0),
            SolarInverterServer(inverter_id=2, host="127.0.0.1", port=base_port + 1, virtual_ip="192.168.10.12", rated_capacity_kw=2500.0),
            SolarInverterServer(inverter_id=3, host="127.0.0.1", port=base_port + 2, virtual_ip="192.168.10.13", rated_capacity_kw=2500.0),
        ]

    def start_all(self):
        for inv in self.inverters:
            inv.start()
        logger.info("All 3 Inverter Modbus TCP servers started.")

    def stop_all(self):
        for inv in self.inverters:
            inv.stop()
        logger.info("All Inverter servers stopped.")

if __name__ == "__main__":
    cluster = InverterCluster()
    cluster.start_all()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cluster.stop_all()

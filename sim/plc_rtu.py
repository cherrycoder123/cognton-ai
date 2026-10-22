#!/usr/bin/env python3
"""
sim/plc_rtu.py
Simulates an Inverter Control Room (ICR) RTU / PLC (e.g. OpenPLC / ABB RTU560).
Coordinates substation breakers, plant weather station (pyranometer / solar irradiance),
transformer temperatures, and physical ICR perimeter security contacts.
Listens on Modbus TCP port 5024 (simulated IP 192.168.10.20).
"""

import socket
import struct
import threading
import time
import random
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [RTU/PLC] %(message)s")
logger = logging.getLogger("RTU_PLC")

class ICRProgrammableLogicController:
    def __init__(self, host: str = "127.0.0.1", port: int = 5024, virtual_ip: str = "192.168.10.20"):
        self.host = host
        self.port = port
        self.virtual_ip = virtual_ip
        self.running = False
        self.sock = None
        self.clients = []
        
        # Coils (FC 01 / 05 / 15)
        # Coil 0: 33kV Main Feeder Breaker (1 = Closed/Energized, 0 = Open/Tripped)
        # Coil 1: Capacitor Bank 1 (1 = In Service)
        # Coil 2: Inverter Control Power Relay
        # Coil 3: ICR Physical Door Intrusion Alarm (0 = Closed, 1 = Opened)
        self.coils = {
            0: 1,
            1: 1,
            2: 1,
            3: 0,
        }
        
        # Holding Registers (FC 03 / 06 / 16)
        # 0x2000 (8192): Plant Export Limit MW * 10 (e.g. 75 = 7.5 MW)
        # 0x2001 (8193): Reactive Power Target Q (kVAR)
        # 0x2002 (8194): High Voltage Protection Threshold (kV * 10)
        self.holding_registers = {
            8192: 75,
            8193: 0,
            8194: 345, # 34.5 kV
        }
        
        # Input Registers (FC 04)
        # 0: Weather Station Irradiance (W/m2, e.g. 880)
        # 1: Ambient Temperature (deg C * 10, e.g. 425 = 42.5 C in Khavda desert)
        # 2: Wind Speed (m/s * 10, e.g. 45 = 4.5 m/s)
        # 3: 33kV Bus Voltage (kV * 100, e.g. 3310 = 33.10 kV)
        # 4: 33kV Bus Current (A, e.g. 130 A)
        # 5: Total Active Power Exported (kW, e.g. 7350 kW)
        # 6: Step-Up Transformer Oil Temperature (deg C)
        self.input_registers = {
            0: 880,
            1: 425,
            2: 45,
            3: 3310,
            4: 130,
            5: 7350,
            6: 58,
        }
        
        self.lock = threading.Lock()
        self.physics_thread = None

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(10)
        
        self.physics_thread = threading.Thread(target=self._update_loop, daemon=True)
        self.physics_thread.start()
        
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        logger.info(f"ICR RTU/PLC listening on {self.host}:{self.port} (Virtual IP: {self.virtual_ip})")

    def _update_loop(self):
        while self.running:
            time.sleep(1.0)
            with self.lock:
                # Solar desert dynamics (Khavda, Gujarat)
                self.input_registers[0] = int(880 + random.uniform(-15, 15))
                self.input_registers[1] = int(420 + random.uniform(-5, 8)) # 42°C
                self.input_registers[2] = int(48 + random.uniform(-8, 8))
                
                # Check breaker status
                if self.coils.get(0, 1) == 1:
                    self.input_registers[3] = int(3300 + random.uniform(-10, 10))
                    self.input_registers[4] = int(128 + random.uniform(-4, 4))
                    self.input_registers[5] = int(7350 + random.uniform(-50, 50))
                    self.input_registers[6] = int(58 + random.uniform(-1, 1))
                else:
                    self.input_registers[3] = 0
                    self.input_registers[4] = 0
                    self.input_registers[5] = 0
                    self.input_registers[6] = max(42, self.input_registers[6] - 0.2)

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
        
        # FC 01: Read Coils
        if fc == 1:
            start_addr, count = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                coil_bytes = bytearray((count + 7) // 8)
                for i in range(count):
                    bit_val = self.coils.get(start_addr + i, 0)
                    if bit_val:
                        coil_bytes[i // 8] |= (1 << (i % 8))
                resp = struct.pack(">BB", fc, len(coil_bytes)) + bytes(coil_bytes)
                return resp

        # FC 05: Write Single Coil
        elif fc == 5:
            coil_addr, val = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                bit_val = 1 if val == 0xFF00 else 0
                self.coils[coil_addr] = bit_val
                logger.warning(f"[RTU/PLC] Modbus WRITE COIL addr={coil_addr} val={'CLOSED/ON' if bit_val else 'OPEN/OFF'}")
                return pdu

        # FC 03: Read Holding Registers
        elif fc == 3:
            start_addr, count = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                byte_count = count * 2
                vals = [self.holding_registers.get(start_addr + i, 0) for i in range(count)]
                resp = struct.pack(">BB", fc, byte_count)
                for v in vals:
                    resp += struct.pack(">H", v & 0xFFFF)
                return resp

        # FC 04: Read Input Registers
        elif fc == 4:
            start_addr, count = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                byte_count = count * 2
                vals = [self.input_registers.get(start_addr + i, 0) for i in range(count)]
                resp = struct.pack(">BB", fc, byte_count)
                for v in vals:
                    resp += struct.pack(">H", v & 0xFFFF)
                return resp

        # FC 06: Write Single Holding Register
        elif fc == 6:
            reg_addr, val = struct.unpack(">HH", pdu[1:5])
            with self.lock:
                self.holding_registers[reg_addr] = val
                logger.warning(f"[RTU/PLC] Modbus WRITE REG addr=0x{reg_addr:04X} val={val}")
                return pdu

        else:
            return struct.pack(">BB", fc | 0x80, 0x01)

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

if __name__ == "__main__":
    plc = ICRProgrammableLogicController()
    plc.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        plc.stop()

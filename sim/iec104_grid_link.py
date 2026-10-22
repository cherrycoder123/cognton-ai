#!/usr/bin/env python3
"""
sim/iec104_grid_link.py
Simulates the IEC 60870-5-104 telecontrol link connecting the Solar Park Pooling Substation
to the State Load Despatch Centre (SLDC / Grid Control Centre).
Implements full APCI (U/S/I frames) and ASDU parsing/generation (Measured Float M_ME_NC_1,
Single Command C_SC_NA_1).
Runs on Port 2404 (simulated IP 192.168.10.30 -> Grid EMS 10.100.0.10).
"""

import socket
import struct
import threading
import time
import random
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [IEC-104] %(message)s")
logger = logging.getLogger("IEC104")

START_BYTE = 0x68

# U-Format APCI Types
U_STARTDT_ACT = 0x07
U_STARTDT_CON = 0x0B
U_STOPDT_ACT  = 0x13
U_STOPDT_CON  = 0x23
U_TESTFR_ACT  = 0x43
U_TESTFR_CON  = 0x83

# ASDU Type IDs
M_SP_NA_1 = 1    # Single-point information
M_ME_NC_1 = 13   # Measured value, short floating point number
C_SC_NA_1 = 45   # Single command (SLDC Grid Dispatch / Trip)
C_SE_NC_1 = 50   # Set-point command, short floating point

class IEC104SubstationServer:
    """Substation Controlled Station (Slave) answering to Grid Control Centre (Master)."""
    def __init__(self, host: str = "127.0.0.1", port: int = 2404, virtual_ip: str = "192.168.10.30"):
        self.host = host
        self.port = port
        self.virtual_ip = virtual_ip
        self.running = False
        self.sock = None
        self.clients = []
        self.send_seq = 0
        self.recv_seq = 0
        self.data_transfer_enabled = False
        
        # Substation live telemetry values
        self.total_power_mw = 7.35
        self.grid_freq_hz = 50.01
        self.bus_voltage_kv = 33.12
        self.breaker_closed = True

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        
        telemetry_t = threading.Thread(target=self._spontaneous_telemetry_loop, daemon=True)
        telemetry_t.start()
        logger.info(f"Substation IEC-104 Server listening on {self.host}:{self.port} (Virtual IP: {self.virtual_ip})")

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
        logger.info(f"IEC-104 Client connected from {addr}")
        while self.running:
            try:
                # Read APCI header (at least 6 bytes: 0x68, Length, 4 bytes control)
                buf = conn.recv(6)
                if not buf or len(buf) < 6:
                    break
                if buf[0] != START_BYTE:
                    continue
                apdu_len = buf[1]
                ctrl = buf[2:6]
                
                # Check frame format
                b0 = ctrl[0]
                if (b0 & 0x03) == 0x03:
                    # U-Format frame
                    u_type = b0
                    if u_type == U_STARTDT_ACT:
                        logger.info("IEC-104 Received STARTDT act -> Sending STARTDT con")
                        self.data_transfer_enabled = True
                        resp = bytes([START_BYTE, 0x04, U_STARTDT_CON, 0x00, 0x00, 0x00])
                        conn.sendall(resp)
                    elif u_type == U_TESTFR_ACT:
                        resp = bytes([START_BYTE, 0x04, U_TESTFR_CON, 0x00, 0x00, 0x00])
                        conn.sendall(resp)
                    elif u_type == U_STOPDT_ACT:
                        self.data_transfer_enabled = False
                        resp = bytes([START_BYTE, 0x04, U_STOPDT_CON, 0x00, 0x00, 0x00])
                        conn.sendall(resp)

                elif (b0 & 0x01) == 0x01:
                    # S-Format frame
                    rx = (struct.unpack("<H", ctrl[2:4])[0]) >> 1
                    self.recv_seq = rx

                else:
                    # I-Format frame (Information transfer containing ASDU)
                    tx = (struct.unpack("<H", ctrl[0:2])[0]) >> 1
                    rx = (struct.unpack("<H", ctrl[2:4])[0]) >> 1
                    self.recv_seq = tx + 1
                    
                    # Read remainder of APDU (ASDU)
                    asdu_len = apdu_len - 4
                    asdu_data = b""
                    while len(asdu_data) < asdu_len:
                        chunk = conn.recv(asdu_len - len(asdu_data))
                        if not chunk:
                            break
                        asdu_data += chunk
                    
                    if asdu_data:
                        self._process_asdu(conn, asdu_data)
            except Exception:
                break
        try:
            conn.close()
            if conn in self.clients:
                self.clients.remove(conn)
        except Exception:
            pass

    def _process_asdu(self, conn, asdu: bytes):
        if len(asdu) < 6:
            return
        type_id, vsq, cot, ca = struct.unpack("<BBHH", asdu[0:6])
        logger.info(f"IEC-104 Received ASDU Type={type_id}, COT={cot}, CA={ca}")
        
        # C_SC_NA_1 (Single Command, e.g. Trip / Close Breaker or SLDC Dispatch)
        if type_id == C_SC_NA_1 and len(asdu) >= 10:
            ioa = asdu[6] | (asdu[7] << 8) | (asdu[8] << 16)
            sco = asdu[9] # Single command state: 0 = Off/Open, 1 = On/Close
            val = sco & 0x01
            logger.warning(f"IEC-104 COMMAND RECEIVED: IOA={ioa} Command={'CLOSE' if val else 'TRIP/OPEN'} (SCO=0x{sco:02X})")
            if ioa == 1001:
                self.breaker_closed = bool(val)
            
            # Send S-Format ACK
            s_frame = struct.pack("<BBHH", START_BYTE, 0x04, 0x01, (self.recv_seq << 1))
            conn.sendall(s_frame)

    def _spontaneous_telemetry_loop(self):
        """Spontaneously transmit Short Float telemetry (M_ME_NC_1) to connected SLDC."""
        while self.running:
            time.sleep(2.0)
            if not self.data_transfer_enabled or not self.clients:
                continue

            self.total_power_mw = round(7.35 + random.uniform(-0.15, 0.15), 2)
            self.grid_freq_hz = round(50.00 + random.uniform(-0.03, 0.03), 3)

            # Build ASDU Type 13 (M_ME_NC_1)
            # IOA 101: Active Power MW
            # IOA 102: Frequency Hz
            type_id = M_ME_NC_1
            vsq = 0x01 # 1 object
            cot = 3    # Spontaneous
            ca = 1     # Common address
            ioa = 101
            ioa_bytes = struct.pack("<I", ioa)[:3]
            float_val = struct.pack("<f", self.total_power_mw)
            qds = b"\x00" # Good quality
            
            asdu = struct.pack("<BBHH", type_id, vsq, cot, ca) + ioa_bytes + float_val + qds
            apdu_len = 4 + len(asdu)
            
            tx_seq = (self.send_seq << 1) & 0xFFFE
            rx_seq = (self.recv_seq << 1) & 0xFFFE
            apci = struct.pack("<BBHH", START_BYTE, apdu_len, tx_seq, rx_seq)
            
            packet = apci + asdu
            self.send_seq += 1
            
            dead = []
            for c in list(self.clients):
                try:
                    c.sendall(packet)
                except Exception:
                    dead.append(c)
            for d in dead:
                if d in self.clients:
                    self.clients.remove(d)

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


class FakeGridControlCenterClient:
    """Simulates State Load Despatch Centre (SLDC / EMS) polling telemetry and sending heartbeat."""
    def __init__(self, target_host: str = "127.0.0.1", target_port: int = 2404, virtual_ip: str = "10.100.0.10"):
        self.target_host = target_host
        self.target_port = target_port
        self.virtual_ip = virtual_ip
        self.sock = None
        self.running = False
        self.connected = False
        self.latest_telemetry = {}

    def start(self):
        self.running = True
        t = threading.Thread(target=self._client_loop, daemon=True)
        t.start()
        logger.info(f"Fake Grid Control Centre (EMS/SLDC) started, targeting {self.target_host}:{self.target_port}")

    def _client_loop(self):
        while self.running:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.connect((self.target_host, self.target_port))
                self.connected = True
                logger.info("SLDC Client connected to Substation IEC-104 Server")
                
                # Send STARTDT act
                startdt = bytes([START_BYTE, 0x04, U_STARTDT_ACT, 0x00, 0x00, 0x00])
                self.sock.sendall(startdt)
                
                # Receive loop
                while self.running:
                    header = self.sock.recv(6)
                    if not header or len(header) < 6:
                        break
                    if header[0] != START_BYTE:
                        continue
                    apdu_len = header[1]
                    ctrl = header[2:6]
                    asdu_len = apdu_len - 4
                    if asdu_len > 0:
                        asdu_data = b""
                        while len(asdu_data) < asdu_len:
                            chunk = self.sock.recv(asdu_len - len(asdu_data))
                            if not chunk:
                                break
                            asdu_data += chunk
                        if asdu_data and asdu_data[0] == M_ME_NC_1:
                            val = struct.unpack("<f", asdu_data[9:13])[0]
                            self.latest_telemetry["export_mw"] = round(val, 2)
            except Exception:
                self.connected = False
                time.sleep(2.0)

    def send_curtailment_command(self, trip_breaker: bool = False):
        """Sends an IEC-104 C_SC_NA_1 Single Command to substation."""
        if not self.sock or not self.connected:
            return False
        try:
            type_id = C_SC_NA_1
            vsq = 1
            cot = 6 # Activation
            ca = 1
            ioa = 1001
            ioa_bytes = struct.pack("<I", ioa)[:3]
            sco = 0x01 if not trip_breaker else 0x00 # 1=Close, 0=Trip
            asdu = struct.pack("<BBHH", type_id, vsq, cot, ca) + ioa_bytes + bytes([sco])
            apdu_len = 4 + len(asdu)
            apci = struct.pack("<BBHH", START_BYTE, apdu_len, 0x0000, 0x0000)
            self.sock.sendall(apci + asdu)
            logger.info(f"SLDC dispatched command: {'TRIP' if trip_breaker else 'CLOSE'}")
            return True
        except Exception as e:
            logger.error(f"Failed to send dispatch command: {e}")
            return False

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

if __name__ == "__main__":
    server = IEC104SubstationServer()
    server.start()
    time.sleep(1)
    client = FakeGridControlCenterClient()
    client.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.stop()
        server.stop()

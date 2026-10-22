#!/usr/bin/env python3
"""
sensor/parsers/iec104_parser.py
Passive IEC 60870-5-104 Protocol Dissector.
Parses APCI headers (U-format, S-format, I-format) and ASDU structures.
Detects command injections, trip signals, and frame anomalies passively.
"""

import struct
from typing import Optional, Dict, Any

START_BYTE = 0x68

ASDU_NAMES = {
    1: "M_SP_NA_1 (Single Point)",
    3: "M_DP_NA_1 (Double Point)",
    9: "M_ME_NA_1 (Measured Normalized)",
    11: "M_ME_NB_1 (Measured Scaled)",
    13: "M_ME_NC_1 (Measured Short Float)",
    30: "M_SP_TB_1 (Single Point with Time)",
    45: "C_SC_NA_1 (Single Command - Trip/Close)",
    46: "C_DC_NA_1 (Double Command)",
    48: "C_SE_NA_1 (Set-point Normalized)",
    50: "C_SE_NC_1 (Set-point Short Float)",
    100: "C_IC_NA_1 (Interrogation Command)",
    105: "C_RP_NA_1 (Reset Process Command)",
}

class IEC104Frame:
    def __init__(self,
                 frame_type: str, # "U", "S", "I", or "MALFORMED"
                 apdu_len: int,
                 u_function: Optional[str] = None,
                 tx_seq: Optional[int] = None,
                 rx_seq: Optional[int] = None,
                 asdu_type: Optional[int] = None,
                 asdu_name: Optional[str] = None,
                 cause_of_trans: Optional[int] = None,
                 common_addr: Optional[int] = None,
                 ioa: Optional[int] = None,
                 is_command: bool = False,
                 command_value: Optional[Any] = None,
                 float_value: Optional[float] = None,
                 raw_bytes: bytes = b""):
        self.frame_type = frame_type
        self.apdu_len = apdu_len
        self.u_function = u_function
        self.tx_seq = tx_seq
        self.rx_seq = rx_seq
        self.asdu_type = asdu_type
        self.asdu_name = asdu_name
        self.cause_of_trans = cause_of_trans
        self.common_addr = common_addr
        self.ioa = ioa
        self.is_command = is_command
        self.command_value = command_value
        self.float_value = float_value
        self.raw_bytes = raw_bytes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame_type": self.frame_type,
            "apdu_len": self.apdu_len,
            "u_function": self.u_function,
            "tx_seq": self.tx_seq,
            "rx_seq": self.rx_seq,
            "asdu_type": self.asdu_type,
            "asdu_name": self.asdu_name,
            "cause_of_trans": self.cause_of_trans,
            "common_addr": self.common_addr,
            "ioa": self.ioa,
            "is_command": self.is_command,
            "command_value": self.command_value,
            "float_value": self.float_value,
        }

class IEC104Parser:
    @staticmethod
    def parse(payload: bytes) -> Optional[IEC104Frame]:
        if len(payload) < 6:
            return None
        
        if payload[0] != START_BYTE:
            return None
        
        apdu_len = payload[1]
        ctrl = payload[2:6]
        b0 = ctrl[0]
        
        # Check APCI Format
        if (b0 & 0x03) == 0x03:
            # U-Format
            u_types = {
                0x07: "STARTDT_ACT",
                0x0B: "STARTDT_CON",
                0x13: "STOPDT_ACT",
                0x23: "STOPDT_CON",
                0x43: "TESTFR_ACT",
                0x83: "TESTFR_CON",
            }
            func = u_types.get(b0, f"U_UNKNOWN_0x{b0:02X}")
            return IEC104Frame(frame_type="U", apdu_len=apdu_len, u_function=func, raw_bytes=payload)
        
        elif (b0 & 0x01) == 0x01:
            # S-Format
            rx_seq = (struct.unpack("<H", ctrl[2:4])[0]) >> 1
            return IEC104Frame(frame_type="S", apdu_len=apdu_len, rx_seq=rx_seq, raw_bytes=payload)
        
        else:
            # I-Format
            tx_seq = (struct.unpack("<H", ctrl[0:2])[0]) >> 1
            rx_seq = (struct.unpack("<H", ctrl[2:4])[0]) >> 1
            
            asdu = payload[6:]
            if len(asdu) < 6:
                return IEC104Frame(frame_type="I", apdu_len=apdu_len, tx_seq=tx_seq, rx_seq=rx_seq, raw_bytes=payload)
            
            type_id, vsq, cot, ca = struct.unpack("<BBHH", asdu[0:6])
            asdu_name = ASDU_NAMES.get(type_id, f"ASDU_{type_id}")
            
            ioa = None
            is_cmd = type_id in (45, 46, 48, 50, 100, 105)
            cmd_val = None
            flt_val = None
            
            if len(asdu) >= 9:
                ioa = asdu[6] | (asdu[7] << 8) | (asdu[8] << 16)
                
            if type_id == 45 and len(asdu) >= 10:
                # Single Command: bit 0 of SCO is state (1 = on/close, 0 = off/trip)
                sco = asdu[9]
                cmd_val = "CLOSE/ON" if (sco & 0x01) else "TRIP/OPEN"
            elif type_id == 13 and len(asdu) >= 13:
                # Measured Short Float
                flt_val = struct.unpack("<f", asdu[9:13])[0]

            return IEC104Frame(
                frame_type="I",
                apdu_len=apdu_len,
                tx_seq=tx_seq,
                rx_seq=rx_seq,
                asdu_type=type_id,
                asdu_name=asdu_name,
                cause_of_trans=cot,
                common_addr=ca,
                ioa=ioa,
                is_command=is_cmd,
                command_value=cmd_val,
                float_value=flt_val,
                raw_bytes=payload
            )

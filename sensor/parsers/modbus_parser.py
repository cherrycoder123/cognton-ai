#!/usr/bin/env python3
"""
sensor/parsers/modbus_parser.py
Passive Modbus TCP Frame Parser and Dissector.
Decodes MBAP and PDU without altering or injecting packets into the control network.
Extracts transaction details, function codes, register addresses, quantities, and written payloads.
"""

import struct
from typing import Optional, Dict, Any, List

FUNCTION_NAMES = {
    1: "READ_COILS",
    2: "READ_DISCRETE_INPUTS",
    3: "READ_HOLDING_REGISTERS",
    4: "READ_INPUT_REGISTERS",
    5: "WRITE_SINGLE_COIL",
    6: "WRITE_SINGLE_REGISTER",
    15: "WRITE_MULTIPLE_COILS",
    16: "WRITE_MULTIPLE_REGISTERS",
    8: "DIAGNOSTICS",
    43: "ENCAPSULATED_INTERFACE_TRANSPORT",
    90: "PROPRIETARY_FIRMWARE_DOWNLOAD",
}

class ModbusFrame:
    def __init__(self,
                 trans_id: int,
                 proto_id: int,
                 length: int,
                 unit_id: int,
                 func_code: int,
                 is_exception: bool,
                 func_name: str,
                 is_write: bool,
                 start_address: Optional[int] = None,
                 count: Optional[int] = None,
                 values: Optional[List[int]] = None,
                 exception_code: Optional[int] = None,
                 raw_bytes: bytes = b""):
        self.trans_id = trans_id
        self.proto_id = proto_id
        self.length = length
        self.unit_id = unit_id
        self.func_code = func_code
        self.is_exception = is_exception
        self.func_name = func_name
        self.is_write = is_write
        self.start_address = start_address
        self.count = count
        self.values = values or []
        self.exception_code = exception_code
        self.raw_bytes = raw_bytes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trans_id": self.trans_id,
            "unit_id": self.unit_id,
            "func_code": self.func_code,
            "func_name": self.func_name,
            "is_write": self.is_write,
            "start_address": self.start_address,
            "count": self.count,
            "values": self.values,
            "is_exception": self.is_exception,
            "exception_code": self.exception_code,
        }

class ModbusParser:
    @staticmethod
    def parse(payload: bytes, is_response: bool = False) -> Optional[ModbusFrame]:
        """
        Parses a raw Modbus TCP payload.
        Returns ModbusFrame or None if payload is invalid.
        """
        if len(payload) < 8:
            return None
        
        try:
            trans_id, proto_id, length, unit_id = struct.unpack(">HHHB", payload[0:7])
            if proto_id != 0:
                # Not Modbus protocol (protocol ID must be 0)
                return None
            
            pdu = payload[7:7 + length - 1] if len(payload) >= 7 + length - 1 else payload[7:]
            if not pdu:
                return None
            
            raw_fc = pdu[0]
            is_exception = bool(raw_fc & 0x80)
            fc = raw_fc & 0x7F
            func_name = FUNCTION_NAMES.get(fc, f"UNKNOWN_FC_{fc}")
            
            is_write = fc in (5, 6, 15, 16, 90)
            start_addr = None
            count = None
            values = []
            exc_code = None

            if is_exception:
                exc_code = pdu[1] if len(pdu) > 1 else None
                return ModbusFrame(trans_id, proto_id, length, unit_id, fc,
                                   True, func_name, is_write, exception_code=exc_code,
                                   raw_bytes=payload)

            if not is_response:
                # REQUEST
                if fc in (1, 2, 3, 4):
                    if len(pdu) >= 5:
                        start_addr, count = struct.unpack(">HH", pdu[1:5])
                elif fc in (5, 6):
                    if len(pdu) >= 5:
                        start_addr, val = struct.unpack(">HH", pdu[1:5])
                        count = 1
                        values = [val]
                elif fc in (15, 16):
                    if len(pdu) >= 6:
                        start_addr, count, byte_cnt = struct.unpack(">HHB", pdu[1:6])
                        if fc == 16 and len(pdu) >= 6 + byte_cnt:
                            for i in range(count):
                                reg_val = struct.unpack(">H", pdu[6 + i*2 : 8 + i*2])[0]
                                values.append(reg_val)
            else:
                # RESPONSE
                if fc in (1, 2, 3, 4):
                    if len(pdu) >= 2:
                        byte_cnt = pdu[1]
                        if fc in (3, 4) and len(pdu) >= 2 + byte_cnt:
                            for i in range(byte_cnt // 2):
                                val = struct.unpack(">H", pdu[2 + i*2 : 4 + i*2])[0]
                                values.append(val)
                elif fc in (5, 6):
                    if len(pdu) >= 5:
                        start_addr, val = struct.unpack(">HH", pdu[1:5])
                        count = 1
                        values = [val]

            return ModbusFrame(
                trans_id=trans_id,
                proto_id=proto_id,
                length=length,
                unit_id=unit_id,
                func_code=fc,
                is_exception=is_exception,
                func_name=func_name,
                is_write=is_write,
                start_address=start_addr,
                count=count,
                values=values,
                raw_bytes=payload
            )
        except Exception:
            return None

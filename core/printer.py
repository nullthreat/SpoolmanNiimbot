"""
Minimal, self-contained Niimbot printer client for the B1 over USB (serial).

The protocol implementation is adapted from the open-source niimprint project
(MIT, originally by kjy00302, forked by AndBondStyle):
    https://github.com/AndBondStyle/niimprint

Only the USB/serial transport is included here because that is the supported
connection for this tool. No Bluetooth / pybluez dependency.
"""

import enum
import logging
import math
import struct
import time

import serial
from PIL import Image, ImageOps
from serial.tools.list_ports import comports as list_comports

log = logging.getLogger("niimbot")


class NiimbotPacket:
    """A single frame of the Niimbot serial protocol."""

    def __init__(self, type_: int, data: bytes):
        self.type = type_
        self.data = data

    @classmethod
    def from_bytes(cls, pkt: bytes) -> "NiimbotPacket":
        assert pkt[:2] == b"\x55\x55", "bad packet start"
        assert pkt[-2:] == b"\xaa\xaa", "bad packet end"
        type_ = pkt[2]
        length = pkt[3]
        data = pkt[4 : 4 + length]
        # checksum = pkt[-3]  # not validated; printer doesn't require it
        return cls(type_, data)

    def to_bytes(self) -> bytes:
        checksum = self.type ^ len(self.data)
        for b in self.data:
            checksum ^= b
        return bytes(
            (0x55, 0x55, self.type, len(self.data), *self.data, checksum, 0xAA, 0xAA)
        )


class RequestCodeEnum(enum.IntEnum):
    GET_INFO = 0x40
    GET_RFID = 0x1A
    HEARTBEAT = 0xDC
    SET_LABEL_TYPE = 0x23
    SET_LABEL_DENSITY = 0x21
    START_PRINT = 0x01
    END_PRINT = 0xF3
    START_PAGE_PRINT = 0x03
    END_PAGE_PRINT = 0xE3
    SET_DIMENSION = 0x13
    SET_QUANTITY = 0x15
    GET_PRINT_STATUS = 0xA3


class InfoEnum(enum.IntEnum):
    DEVICESERIAL = 11
    SOFTVERSION = 9
    HARDVERSION = 12
    BATTERY = 10


def find_serial_port(preferred: str | None = None) -> str:
    """Return a serial port path. If preferred is given, use it. Otherwise
    auto-detect; raise a helpful error if 0 or >1 candidates exist."""
    if preferred:
        return preferred
    ports = list(list_comports())
    if not ports:
        raise RuntimeError(
            "No serial ports found. Plug in the Niimbot B1 over USB and power it on, "
            "then check Device Manager for the COM port."
        )
    if len(ports) > 1:
        lines = [f"  {p.device} : {p.description} [{p.hwid}]" for p in ports]
        raise RuntimeError(
            "Multiple serial ports found. Pass the right one with --port:\n"
            + "\n".join(lines)
        )
    return ports[0].device


class SerialTransport:
    def __init__(self, port: str):
        self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.5)

    def read(self, length: int) -> bytes:
        return self._serial.read(length)

    def write(self, data: bytes):
        return self._serial.write(data)

    def close(self):
        self._serial.close()


class PrinterClient:
    def __init__(self, transport: SerialTransport):
        self._transport = transport
        self._packetbuf = bytearray()

    # --- high level ----------------------------------------------------------

    def print_image(self, image: Image.Image, density: int = 3, quantity: int = 1, line_delay: float = 0.01):
        self.set_label_density(density)
        self.set_label_type(1)
        self.start_print()
        self.start_page_print()
        self.set_dimension(image.height, image.width)
        self.set_quantity(quantity)

        # --- DO NOT "SIMPLIFY" THIS SEQUENCE ---
        # This exact order is what prints full labels on the B1. Verified by
        # hand after several regressions. Three load-bearing parts:
        #   1. the per-line delay (prevents the receive buffer overflowing),
        #   2. periodically READING the printer's return bytes DURING the send
        #      (prevents the printer's transmit buffer filling and stalling the
        #      burn via serial flow control), and
        #   3. the get_print_status poll BELOW, which holds off end_print until
        #      the printer has actually burned the whole page.
        # Symptoms if you remove each: (1)/(2) -> blank label; (3) -> only the
        # top fraction of the page prints. Keep all three.

        # 1. Pace the line packets so the printer's receive buffer doesn't
        #    overflow. Also periodically drain the printer's RETURN bytes while
        #    sending: if we never read, its transmit buffer fills and serial
        #    flow control stalls the burn, producing a blank label. Verified by
        #    hand -- draining during the send is what makes it print reliably.
        for i, pkt in enumerate(self._encode_image(image)):
            self._send(pkt)
            time.sleep(line_delay)
            if i % 40 == 39:
                self._transport.read(128)  # discard return bytes

        # Drop any partial return bytes so the status poll below reads clean.
        self._packetbuf.clear()

        # 2. Wait for the page to be acknowledged.
        deadline = time.time() + 15
        while not self.end_page_print():
            if time.time() > deadline:
                log.warning("end_page_print not acknowledged after 15s")
                break
            time.sleep(0.05)

        # 3. Poll until the printer reports it has printed all requested pages.
        #    This is what makes the FULL page print rather than just the top.
        deadline = time.time() + 20
        while time.time() < deadline:
            status = self.get_print_status()
            if status and status.get("page", 0) >= quantity:
                break
            time.sleep(0.1)

        self.end_print()

    def _encode_image(self, image: Image.Image):
        img = ImageOps.invert(image.convert("L")).convert("1")
        for y in range(img.height):
            bits = "".join(
                "0" if img.getpixel((x, y)) == 0 else "1" for x in range(img.width)
            )
            line_data = int(bits, 2).to_bytes(math.ceil(img.width / 8), "big")
            header = struct.pack(">H3BB", y, 0, 0, 0, 1)
            yield NiimbotPacket(0x85, header + line_data)

    # --- low level -----------------------------------------------------------

    def _send(self, packet: NiimbotPacket):
        self._transport.write(packet.to_bytes())

    def _recv(self):
        packets = []
        self._packetbuf.extend(self._transport.read(1024))
        while len(self._packetbuf) > 4:
            pkt_len = self._packetbuf[3] + 7
            if len(self._packetbuf) >= pkt_len:
                packets.append(NiimbotPacket.from_bytes(self._packetbuf[:pkt_len]))
                del self._packetbuf[:pkt_len]
            else:
                break
        return packets

    def _transceive(self, reqcode, data, respoffset=1):
        respcode = respoffset + reqcode
        self._send(NiimbotPacket(reqcode, data))
        resp = None
        for _ in range(6):
            for packet in self._recv():
                if packet.type == 219:
                    raise ValueError("printer reported an error")
                elif packet.type == 0:
                    raise NotImplementedError
                elif packet.type == respcode:
                    resp = packet
            if resp:
                return resp
            time.sleep(0.1)
        return resp

    def _packet_to_int(self, p):
        return int.from_bytes(p.data, "big")

    def get_info(self, key: int):
        p = self._transceive(RequestCodeEnum.GET_INFO, bytes((key,)), key)
        if not p:
            return None
        if key == InfoEnum.DEVICESERIAL:
            return p.data.hex()
        if key in (InfoEnum.SOFTVERSION, InfoEnum.HARDVERSION):
            return self._packet_to_int(p) / 100
        return self._packet_to_int(p)

    def heartbeat(self):
        return self._transceive(RequestCodeEnum.HEARTBEAT, b"\x01")

    def set_label_type(self, n):
        assert 1 <= n <= 3
        return bool(self._transceive(RequestCodeEnum.SET_LABEL_TYPE, bytes((n,)), 16).data[0])

    def set_label_density(self, n):
        assert 1 <= n <= 5
        return bool(self._transceive(RequestCodeEnum.SET_LABEL_DENSITY, bytes((n,)), 16).data[0])

    def start_print(self, retries: int = 3):
        # The printer occasionally returns a transient error if it is still
        # settling from a previous print. Retry a few times before giving up.
        for attempt in range(retries):
            try:
                return bool(self._transceive(RequestCodeEnum.START_PRINT, b"\x01").data[0])
            except ValueError:
                if attempt == retries - 1:
                    raise
                log.warning("start_print rejected (printer busy?), retrying...")
                time.sleep(0.5)

    def end_print(self):
        return bool(self._transceive(RequestCodeEnum.END_PRINT, b"\x01").data[0])

    def start_page_print(self):
        return bool(self._transceive(RequestCodeEnum.START_PAGE_PRINT, b"\x01").data[0])

    def end_page_print(self):
        return bool(self._transceive(RequestCodeEnum.END_PAGE_PRINT, b"\x01").data[0])

    def set_dimension(self, w, h):
        return bool(self._transceive(RequestCodeEnum.SET_DIMENSION, struct.pack(">HH", w, h)).data[0])

    def set_quantity(self, n):
        return bool(self._transceive(RequestCodeEnum.SET_QUANTITY, struct.pack(">H", n)).data[0])

    def get_print_status(self):
        p = self._transceive(RequestCodeEnum.GET_PRINT_STATUS, b"\x01", 16)
        if not p or len(p.data) < 4:
            return None
        page, progress1, progress2 = struct.unpack(">HBB", p.data[:4])
        return {"page": page, "progress1": progress1, "progress2": progress2}

    def get_rfid(self):
        """Read the RFID tag on the loaded label roll (official Niimbot stock).
        Returns None if no tag / no roll. Dimensions are not encoded in the tag;
        total_len is the number of labels remaining on the roll."""
        p = self._transceive(RequestCodeEnum.GET_RFID, b"\x01")
        if not p:
            return None
        data = p.data
        if len(data) == 0 or data[0] == 0:
            return None
        uuid = data[0:8].hex()
        idx = 8
        barcode_len = data[idx]
        idx += 1
        barcode = data[idx : idx + barcode_len].decode(errors="replace")
        idx += barcode_len
        serial_len = data[idx]
        idx += 1
        serial = data[idx : idx + serial_len].decode(errors="replace")
        idx += serial_len
        total_len, used_len, type_ = struct.unpack(">HHB", data[idx : idx + 5])
        return {
            "uuid": uuid,
            "barcode": barcode,
            "serial": serial,
            "used_len": used_len,
            "total_len": total_len,
            "type": type_,
        }

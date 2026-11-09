"""A minimal MQTT 3.1.1 broker on loopback, for tests that need a real paho client.

It answers CONNECT, PINGREQ and DISCONNECT, and answers a QoS 1 PUBLISH with a PUBACK
only when told to, which is how a printer that applies a command without acknowledging
it is reproduced. Loopback only; no printer, no network.
"""


def read_packet(conn, buf):
    while True:
        if len(buf) >= 2:
            index, multiplier, remaining = 1, 1, 0
            complete = False
            while index < len(buf):
                byte = buf[index]
                remaining += (byte & 127) * multiplier
                multiplier *= 128
                index += 1
                if not byte & 128:
                    complete = True
                    break
            if complete and len(buf) >= index + remaining:
                return buf[: index + remaining], buf[index + remaining :]
        data = conn.recv(4096)
        if not data:
            return None, buf
        buf += data


def serve(server, acknowledge):
    """Minimal MQTT 3.1.1 broker: CONNACK, PINGRESP, and (optionally) PUBACK."""
    conn, _ = server.accept()
    buf = b""
    with conn:
        while True:
            packet, buf = read_packet(conn, buf)
            if packet is None:
                return
            kind = packet[0] >> 4
            if kind == 1:
                conn.sendall(b"\x20\x02\x00\x00")
            elif kind == 3 and (packet[0] >> 1) & 3 == 1 and acknowledge:
                index = 1
                while packet[index] & 128:
                    index += 1
                index += 1
                topic_len = int.from_bytes(packet[index : index + 2], "big")
                conn.sendall(b"\x40\x02" + packet[index + 2 + topic_len : index + 4 + topic_len])
            elif kind == 12:
                conn.sendall(b"\xd0\x00")
            elif kind == 14:
                return

"""
AP-NET point-to-point LoRa MAC layer - the single implementation of the wire format.

This used to be copy-pasted across four files (lora_sender_emulator, lora_receiver_emulator,
dashboard_server, run_demo_queue), which meant every protocol fix had to be applied four
times and the copies had already drifted apart (FIX_PLAN.md D1).

WIRE FORMAT
-----------
Data fragment: 8-byte big-endian header + UTF-8 Base64 text

    !BBHHH   node_id | trans_id | seq_num (1-based) | total_chunks | payload_len

BLOCKNACK: 4-byte big-endian header + N x uint16 sequence numbers

    !BBBB    node_id | trans_id | status (0=SUCCESS, 1=NACK) | count
"""

import struct

# --- Frame geometry ----------------------------------------------------------
HEADER_FORMAT = "!BBHHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)          # 8
BLOCKNACK_FORMAT = "!BBBB"
BLOCKNACK_HEADER_SIZE = struct.calcsize(BLOCKNACK_FORMAT)  # 4

# SX1262 cannot carry more than 255 bytes in a single frame (Planning.md 8.1).
MAX_LORA_FRAME = 255
MAX_FRAGMENT_SIZE = 180                                # 180 + 8 header = 188, comfortably under

# The old code capped a BLOCKNACK at 255 sequence numbers = 4 + 510 = 514 bytes, which no
# real SX1262 could transmit (FIX_PLAN.md E3). Cap at what genuinely fits instead; anything
# beyond simply takes another ARQ round.
MAX_MISSING_PER_BLOCKNACK = (MAX_LORA_FRAME - BLOCKNACK_HEADER_SIZE) // 2   # 125

STATUS_SUCCESS = 0
STATUS_NACK = 1

# Sanity ceiling on total_chunks: 2000 x 180 B = 360 KB, far above any plausible payload.
MAX_TOTAL_CHUNKS = 2000

HEARTBEAT_PREFIX = b"HEARTBEAT:"


# =============================================================================
# Fragment encoding / decoding
# =============================================================================
def make_packet(node_id, trans_id, seq_num, total_chunks, payload):
    """Build one data fragment. `payload` is Base64 text."""
    payload_bytes = payload.encode("utf-8")
    header = struct.pack(HEADER_FORMAT, node_id, trans_id, seq_num, total_chunks, len(payload_bytes))
    return header + payload_bytes


def parse_header(data):
    """
    Return (node_id, trans_id, seq_num, total_chunks, payload_len), or None if the
    frame is too short to hold a header.
    """
    if data is None or len(data) < HEADER_SIZE:
        return None
    return struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])


def extract_payload(data, payload_len):
    """
    Pull the Base64 text out of a fragment.

    Returns None instead of raising when the frame is corrupt (FIX_PLAN.md B5). The old
    code called .decode('utf-8') unguarded, so one damaged byte raised UnicodeDecodeError
    out of the receive loop and silently killed the whole receiver thread.
    """
    if payload_len <= 0 or HEADER_SIZE + payload_len > len(data):
        return None
    try:
        return data[HEADER_SIZE:HEADER_SIZE + payload_len].decode("utf-8")
    except UnicodeDecodeError:
        return None


def fragment_payload(b64_payload, node_id=1, trans_id=1, max_size=MAX_FRAGMENT_SIZE):
    """Split a Base64 string into [(seq_num, packet_bytes), ...] with 1-based sequence numbers."""
    total = (len(b64_payload) + max_size - 1) // max_size
    packets = []
    for idx in range(total):
        chunk = b64_payload[idx * max_size:(idx + 1) * max_size]
        seq = idx + 1
        packets.append((seq, make_packet(node_id, trans_id, seq, total, chunk)))
    return packets


# =============================================================================
# BLOCKNACK encoding / decoding
# =============================================================================
def make_blocknack(node_id, trans_id, status, missing_seqs):
    """Build a BLOCKNACK, truncated to what a real LoRa frame can carry."""
    node_id = node_id if node_id is not None else 0
    trans_id = trans_id if trans_id is not None else 0
    missing_seqs = list(missing_seqs)[:MAX_MISSING_PER_BLOCKNACK]
    header = struct.pack(BLOCKNACK_FORMAT, node_id, trans_id, status, len(missing_seqs))
    return header + b"".join(struct.pack("!H", seq) for seq in missing_seqs)


def parse_blocknack(data):
    """Return (node_id, trans_id, status, [missing...]) or (None, None, None, []) if malformed."""
    if data is None or len(data) < BLOCKNACK_HEADER_SIZE:
        return None, None, None, []

    node_id, trans_id, status, count = struct.unpack(BLOCKNACK_FORMAT, data[:BLOCKNACK_HEADER_SIZE])

    missing = []
    offset = BLOCKNACK_HEADER_SIZE
    for _ in range(count):
        if offset + 2 > len(data):
            break
        missing.append(struct.unpack("!H", data[offset:offset + 2])[0])
        offset += 2

    return node_id, trans_id, status, missing


def is_heartbeat(data):
    return bool(data) and data.startswith(HEARTBEAT_PREFIX)


def parse_heartbeat(data):
    """Return the node id from a `HEARTBEAT:<node>` frame, or None."""
    try:
        parts = data.decode("utf-8").split(":")
    except UnicodeDecodeError:
        return None
    return parts[1].strip() if len(parts) >= 2 and parts[1].strip() else None


# =============================================================================
# Reassembly state machine
# =============================================================================
class ReassemblySession:
    """
    Accumulates fragments for one transaction and reports what is still missing.

    Shared by lora_receiver_emulator.py, dashboard_server.py and run_demo_queue.py so
    all three behave identically.
    """

    def __init__(self, max_total_chunks=MAX_TOTAL_CHUNKS):
        self.max_total_chunks = max_total_chunks
        self.reset()

    def reset(self):
        self.chunks = {}
        self.total_chunks = None
        self.node_id = None
        self.trans_id = None

    @property
    def active(self):
        return self.total_chunks is not None

    def accept(self, data):
        """
        Feed one received frame in.

        Returns one of: "accepted", "malformed", "invalid_total", "out_of_range",
        "bad_payload". Never raises on corrupt input.
        """
        header = parse_header(data)
        if header is None:
            return "malformed"

        node_id, trans_id, seq_num, total_chunks, payload_len = header

        if self.total_chunks is None:
            if total_chunks <= 0 or total_chunks > self.max_total_chunks:
                return "invalid_total"
            self.total_chunks = total_chunks
            self.node_id = node_id
            self.trans_id = trans_id

        if seq_num < 1 or seq_num > self.total_chunks:
            return "out_of_range"

        payload = extract_payload(data, payload_len)
        if payload is None:
            return "bad_payload"

        self.chunks[seq_num] = payload
        return "accepted"

    def missing(self):
        """Sequence numbers not yet received. Empty list when the transfer is complete."""
        if self.total_chunks is None:
            return []
        return [seq for seq in range(1, self.total_chunks + 1) if seq not in self.chunks]

    def progress(self):
        return len(self.chunks), (self.total_chunks or 0)

    def assemble(self):
        """
        Concatenate fragments in sequence order and Base64-decode.

        Raises ValueError if fragments are still missing or the payload will not decode.
        """
        missing = self.missing()
        if missing:
            raise ValueError(f"cannot assemble: {len(missing)} fragments still missing")

        import base64
        full_b64 = "".join(self.chunks[seq] for seq in sorted(self.chunks))
        try:
            return base64.b64decode(full_b64, validate=True)
        except Exception as e:
            raise ValueError(f"Base64 payload did not decode: {e}") from e

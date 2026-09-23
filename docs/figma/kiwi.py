"""Minimal Kiwi decoder — enough to read a .fig scene graph offline.

`.fig` (Figma → File → Save local copy) is a zip. Inside, `canvas.fig` is Figma's
Kiwi binary format: the magic `fig-kiwi`, a version, then two length-prefixed
chunks — the schema (raw deflate) and the document (zstd).

Kiwi is self-describing: the schema travels inside the file (646 type definitions
in ours), so nothing below is hard-coded to Figma's types. If the format changes
under us this raises rather than quietly producing nonsense.

Needs `zstandard`, which is deliberately not a project dependency — run it as
    uv run --with zstandard python docs/figma/snapshot.py --from-fig <file.fig>
"""

import struct

TYPE_NAMES = ["bool", "byte", "int", "uint", "float", "string", "int64", "uint64"]
KINDS = ["ENUM", "STRUCT", "MESSAGE"]


class Reader:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def byte(self) -> int:
        b = self.d[self.i]
        self.i += 1
        return b

    def varuint(self) -> int:
        value = shift = 0
        while True:
            b = self.byte()
            value |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                return value

    def varint(self) -> int:
        v = self.varuint()
        return (v >> 1) ^ -(v & 1)  # zigzag

    def float32(self) -> float:
        """Kiwi stores 0.0 as a single byte; otherwise 4 bytes with the exponent rotated."""
        if self.d[self.i] == 0:
            self.i += 1
            return 0.0
        bits = int.from_bytes(self.d[self.i:self.i + 4], "little")
        self.i += 4
        bits = ((bits << 23) | (bits >> 9)) & 0xFFFFFFFF
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    def string(self) -> str:
        end = self.d.index(b"\x00", self.i)
        out = self.d[self.i:end].decode("utf-8", "replace")
        self.i = end + 1
        return out


def parse_schema(data: bytes) -> list[dict]:
    r = Reader(data)
    defs = []
    for _ in range(r.varuint()):
        name, kind = r.string(), r.byte()
        fields = []
        for _ in range(r.varuint()):
            fields.append({
                "name": r.string(),
                "type": r.varint(),
                "array": bool(r.byte() & 1),
                "value": r.varuint(),
            })
        defs.append({"name": name, "kind": KINDS[kind], "fields": fields})
    return defs


class Decoder:
    def __init__(self, defs: list[dict]):
        self.defs = defs
        self.by_name = {d["name"]: i for i, d in enumerate(defs)}
        self._by_id = [{f["value"]: f for f in d["fields"]} for d in defs]

    def _value(self, r: Reader, ftype: int):
        if ftype >= 0:
            return self.decode(r, ftype)
        kind = TYPE_NAMES[~ftype]
        if kind == "bool":
            return bool(r.byte())
        if kind == "byte":
            return r.byte()
        if kind in ("int", "int64"):
            return r.varint()
        if kind in ("uint", "uint64"):
            return r.varuint()
        if kind == "float":
            return r.float32()
        if kind == "string":
            return r.string()
        raise ValueError(f"unknown builtin type {ftype}")

    def _field(self, r: Reader, field: dict):
        if field["array"]:
            return [self._value(r, field["type"]) for _ in range(r.varuint())]
        return self._value(r, field["type"])

    def decode(self, r: Reader, index: int):
        d = self.defs[index]
        if d["kind"] == "ENUM":
            v = r.varuint()
            return next((f["name"] for f in d["fields"] if f["value"] == v), v)
        if d["kind"] == "STRUCT":
            return {f["name"]: self._field(r, f) for f in d["fields"]}
        out, ids = {}, self._by_id[index]
        while True:
            fid = r.varuint()
            if fid == 0:
                return out
            field = ids.get(fid)
            if field is None:
                raise ValueError(f"unknown field id {fid} in {d['name']}")
            out[field["name"]] = self._field(r, field)


def load(path) -> tuple[list[dict], dict]:
    """(schema definitions, decoded root `Message`) from a .fig file."""
    import zipfile
    import zlib

    try:
        import zstandard
    except ModuleNotFoundError:
        raise SystemExit(
            "The .fig document chunk is zstd-compressed. Re-run with:"
            "  uv run --with zstandard python docs/figma/snapshot.py --from-fig <file.fig>"
        ) from None

    with zipfile.ZipFile(path) as z:
        blob = z.read("canvas.fig")
    if blob[:8] != b"fig-kiwi":
        raise SystemExit(f"{path} does not look like a .fig (magic {blob[:8]!r})")

    off, chunks = 12, []
    while off + 4 <= len(blob):
        (n,) = struct.unpack_from("<I", blob, off)
        off += 4
        if n == 0 or off + n > len(blob):
            break
        chunks.append(blob[off:off + n])
        off += n
    if len(chunks) < 2:
        raise SystemExit("canvas.fig has no document chunk")

    schema = parse_schema(zlib.decompressobj(-15).decompress(chunks[0]))
    document = zstandard.ZstdDecompressor().decompress(chunks[1], max_output_size=512 * 1024 * 1024)
    dec = Decoder(schema)
    return schema, dec.decode(Reader(document), dec.by_name["Message"])

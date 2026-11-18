"""Pack glTF exports as animation-only or mesh-preserving GLBs."""

import base64
import json
from pathlib import Path
import struct
from urllib.parse import unquote, urlsplit


COMPONENT_BYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
COMPONENTS = {"SCALAR": 1, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_gltf(path):
    data = path.read_bytes()
    binary = None
    if path.suffix.lower() == ".glb":
        if len(data) < 20 or struct.unpack_from("<4sII", data) != (b"glTF", 2, len(data)):
            raise ValueError(f"invalid GLB header in {path.name}")
        size, kind = struct.unpack_from("<I4s", data, 12)
        end = 20 + size
        if kind != b"JSON" or size % 4 or end > len(data):
            raise ValueError(f"invalid GLB JSON chunk in {path.name}")
        document = json.loads(data[20:end])
        while end < len(data):
            if len(data) - end < 8:
                raise ValueError(f"incomplete GLB chunk in {path.name}")
            size, kind = struct.unpack_from("<I4s", data, end)
            start, end = end + 8, end + 8 + size
            if size % 4 or end > len(data):
                raise ValueError(f"invalid GLB chunk size in {path.name}")
            if kind == b"BIN\0":
                if binary is not None:
                    raise ValueError(f"multiple GLB binary chunks in {path.name}")
                binary = data[start:end]
    elif path.suffix.lower() == ".gltf":
        document = json.loads(data)
    else:
        raise ValueError("expected a glTF 2.0 export (.gltf or .glb)")
    if document.get("asset", {}).get("version") != "2.0":
        raise ValueError("expected a glTF 2.0 export")
    return document, binary


def read_buffer(path, buffer, embedded):
    uri = buffer.get("uri")
    if uri is None:
        data = embedded
    elif uri.startswith("data:"):
        header, sep, encoded = uri.partition(",")
        if not sep or not header.endswith(";base64"):
            raise ValueError("expected a base64 glTF buffer data URI")
        data = base64.b64decode(encoded, validate=True)
    else:
        url = urlsplit(uri)
        directory = path.resolve().parent
        file = (directory / unquote(url.path)).resolve()
        if url.scheme or url.netloc or url.query or url.fragment or not file.is_relative_to(directory):
            raise ValueError(f"expected a local buffer within {path.name}'s directory")
        data = file.read_bytes()
    length = buffer["byteLength"]
    if data is None or not 0 <= length <= len(data):
        raise ValueError(f"missing or truncated glTF buffer in {path.name}")
    return data[:length]


def animation_glb(path: Path) -> bytes:
    source, embedded = read_gltf(path)
    animations = source.get("animations", [])
    if not animations:
        raise ValueError(f"no animations in {path.name}")
    for animation in animations:
        for channel in animation["channels"]:
            target = channel["target"]
            if target.get("path") not in {"translation", "rotation", "scale"} or "extensions" in target or "node" not in target:
                raise ValueError(f"{animation.get('name', path.name)}: animation needs a node transform target; weights require a mesh")

    buffer = bytearray()
    buffer_views = []
    buffers = {}
    accessor_indices = {}
    accessors = []

    def copy_elements(view_index, offset, count, width):
        if not 0 <= view_index < len(source["bufferViews"]):
            raise ValueError("invalid animation buffer view index")
        view = source["bufferViews"][view_index]
        if view.get("extensions"):
            raise ValueError("compressed or extended animation buffer views are unsupported")
        stride = view.get("byteStride", width)
        size = (count - 1) * stride + width
        if count <= 0 or offset < 0 or stride < width or offset + size > view["byteLength"]:
            raise ValueError("animation accessor exceeds its buffer view")
        index = view["buffer"]
        if not 0 <= index < len(source["buffers"]):
            raise ValueError("invalid animation buffer index")
        if index not in buffers:
            buffers[index] = read_buffer(path, source["buffers"][index], embedded)
        data = buffers[index]
        start = view.get("byteOffset", 0) + offset
        if start < 0 or start + size > len(data):
            raise ValueError("animation buffer view exceeds its buffer")
        buffer.extend(b"\0" * (-len(buffer) % 4))
        destination = len(buffer)
        if stride == width:
            buffer.extend(data[start:start + size])
        else:
            for row in range(count):
                at = start + row * stride
                buffer.extend(data[at:at + width])
        buffer_views.append({"buffer": 0, "byteOffset": destination, "byteLength": count * width})
        return len(buffer_views) - 1

    def copy_accessor(index):
        if index in accessor_indices:
            return accessor_indices[index]
        if not 0 <= index < len(source["accessors"]):
            raise ValueError("invalid animation accessor index")
        accessor = source["accessors"][index].copy()
        if accessor.get("extensions"):
            raise ValueError("extended animation accessors are unsupported")
        width = COMPONENTS[accessor["type"]] * COMPONENT_BYTES[accessor["componentType"]]
        if "bufferView" in accessor:
            accessor["bufferView"] = copy_elements(
                accessor["bufferView"], accessor.pop("byteOffset", 0), accessor["count"], width,
            )
        if "sparse" in accessor:
            sparse = accessor["sparse"].copy()
            for field, width in (
                ("indices", COMPONENT_BYTES[sparse["indices"]["componentType"]]),
                ("values", width),
            ):
                element = sparse[field].copy()
                element["bufferView"] = copy_elements(
                    element["bufferView"], element.pop("byteOffset", 0), sparse["count"], width,
                )
                sparse[field] = element
            accessor["sparse"] = sparse
        accessor_indices[index] = len(accessors)
        accessors.append(accessor)
        return accessor_indices[index]

    document = {"asset": source["asset"]}
    if "scene" in source:
        document["scene"] = source["scene"]
    for field, removed in (
        ("scenes", {"extensions"}),
        ("nodes", {"mesh", "skin", "weights", "camera", "extensions"}),
        ("skins", {"extensions"}),
    ):
        if field in source:
            document[field] = [
                {key: value for key, value in item.items() if key not in removed}
                for item in source[field]
            ]
    for skin in document.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = copy_accessor(skin["inverseBindMatrices"])
    for animation in animations:
        for sampler in animation["samplers"]:
            sampler["input"] = copy_accessor(sampler["input"])
            sampler["output"] = copy_accessor(sampler["output"])
    document["animations"] = animations
    document["accessors"] = accessors
    extensions = {name for animation in animations for name in animation.get("extensions", {})}
    for field in ("extensionsUsed", "extensionsRequired"):
        names = [name for name in source.get(field, []) if name in extensions]
        if names:
            document[field] = names
    if buffer:
        document["buffers"] = [{"byteLength": len(buffer)}]
        document["bufferViews"] = buffer_views
    return encode_glb(document, buffer)


def model_glb(path: Path) -> bytes:
    document, embedded = read_gltf(path)
    buffers = document.get("buffers", [])
    if not document.get("meshes") or len(buffers) != 1:
        raise ValueError("model conversion requires meshes and one glTF buffer")
    if any(
        not image.get("uri") or image["uri"].startswith("data:") or "bufferView" in image
        for image in document.get("images", [])
    ):
        raise ValueError("export model textures as separate files before packing the geometry")
    binary = read_buffer(path, buffers[0], embedded)
    buffers[0].pop("uri", None)
    return encode_glb(document, binary)


def encode_glb(document, buffer):
    text = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text
    if buffer:
        padded_buffer = buffer + b"\0" * (-len(buffer) % 4)
        chunks += struct.pack("<I4s", len(padded_buffer), b"BIN\0") + padded_buffer
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks

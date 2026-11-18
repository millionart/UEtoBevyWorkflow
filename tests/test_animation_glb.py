import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS / "ue_port"))
from animation_glb import read_gltf


class AnimationGlbTest(unittest.TestCase):
    def export(self, directory, target="translation"):
        # The clock shares a strided view with preview data. Only the clock floats belong in GLB.
        clock = struct.pack("<f4sf4s", 0.0, b"MESH", 0.2, b"MESH")
        poses = struct.pack("<6f", 0, 0, 0, 2, 0, 0)
        binds = struct.pack("<16f", *(1.0 if i % 5 == 0 else 0.0 for i in range(16)))
        geometry = b"preview mesh payload"
        data = clock + poses + binds + geometry
        (directory / "preview.bin").write_bytes(data)
        source = directory / "preview.gltf"
        source.write_text(json.dumps({
            "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"name": "Preview", "children": [1], "mesh": 0, "skin": 0},
                      {"name": "root", "translation": [0, 1, 0]}],
            "skins": [{"joints": [1], "skeleton": 1, "inverseBindMatrices": 2}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 3}}]}],
            "buffers": [{"uri": "preview.bin", "byteLength": len(data)}],
            "bufferViews": [
                {"buffer": 0, "byteLength": len(clock), "byteStride": 8},
                {"buffer": 0, "byteOffset": len(clock), "byteLength": len(poses)},
                {"buffer": 0, "byteOffset": len(clock + poses), "byteLength": len(binds)},
                {"buffer": 0, "byteOffset": len(clock + poses + binds), "byteLength": len(geometry)},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 2, "type": "SCALAR",
                 "min": [0], "max": [0.2]},
                {"bufferView": 1, "componentType": 5126, "count": 2, "type": "VEC3"},
                {"bufferView": 2, "componentType": 5126, "count": 1, "type": "MAT4"},
                {"bufferView": 3, "componentType": 5126, "count": 1, "type": "VEC3"},
            ],
            "extensionsUsed": ["ALS_float_curves"],
            "animations": [{
                "name": "Pose", "samplers": [{"input": 0, "output": 1, "interpolation": "STEP"}],
                "channels": [{"sampler": 0, "target": {"node": 1, "path": target}}],
                "extensions": {"ALS_float_curves": {"curves": [{
                    "name": "HandLeftIk", "times": [0], "values": [0.25],
                }]}},
            }],
        }), encoding="utf-8")
        return source, binds + struct.pack("<2f", 0.0, 0.2) + poses

    def convert(self, source, output, kind="animation"):
        return subprocess.run(
            [sys.executable, "-B", str(TOOLS / "port.py"), kind,
             "--gltf", str(source), "--output", str(output)],
            capture_output=True, text=True,
        )

    def test_writes_only_animation_and_binding_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, expected = self.export(directory)
            output = directory / "pose.glb"
            original = source.read_bytes()
            result = self.convert(source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(source.read_bytes(), original)
            document, binary = read_gltf(output)
            self.assertEqual(binary, expected)
            self.assertNotIn("meshes", document)
            self.assertEqual(document["nodes"][0], {"name": "Preview", "children": [1]})
            self.assertEqual(document["skins"], [{
                "joints": [1], "skeleton": 1, "inverseBindMatrices": 0,
            }])
            self.assertEqual(len(document["accessors"]), 3)
            self.assertEqual(document["animations"][0]["samplers"], [{
                "input": 1, "output": 2, "interpolation": "STEP",
            }])
            self.assertEqual(
                document["animations"][0]["extensions"],
                json.loads(original)["animations"][0]["extensions"],
            )
            repacked = directory / "second.glb"
            result = self.convert(output, repacked)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(repacked.read_bytes(), output.read_bytes())

    def test_rejects_mesh_morph_tracks_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, _ = self.export(directory, target="weights")
            output = directory / "pose.glb"
            result = self.convert(source, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("weights require a mesh", result.stderr)
            self.assertFalse(output.exists())

    def test_model_packs_the_full_buffer_and_keeps_external_texture_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, _ = self.export(directory)
            document = json.loads(source.read_bytes())
            document["images"] = [{"uri": "base-color.png"}]
            document["textures"] = [{"source": 0}]
            document["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
            source.write_text(json.dumps(document), encoding="utf-8")
            output = directory / "model.glb"
            result = self.convert(source, output, kind="model")
            self.assertEqual(result.returncode, 0, result.stderr)
            packed, binary = read_gltf(output)
            buffer = document["buffers"][0]
            self.assertEqual(binary[:buffer["byteLength"]], (directory / buffer.pop("uri")).read_bytes())
            self.assertEqual(packed, document)

            document["images"] = [{"bufferView": 3, "mimeType": "image/png"}]
            source.write_text(json.dumps(document), encoding="utf-8")
            output = directory / "embedded.glb"
            result = self.convert(source, output, kind="model")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

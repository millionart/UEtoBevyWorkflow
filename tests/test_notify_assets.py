import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path[:0] = [str(TOOLS), str(TOOLS / "ue_port")]
from animation_notify import notify_record
from notify_assets import build


class NotifyAssetsTest(unittest.TestCase):
    def test_sequence_metadata_drives_the_index_and_generated_track(self):
        owner = "/Game/Animations/Walk.Walk"
        source = {
            "asset_path": owner,
            "asset_class": "/Script/Engine.AnimSequence",
            "animation_timeline": {
                "sequence_length": 1.0,
                "sync_markers": [{"id": "left", "name": "Left", "time": 0.25, "track_index": 0}],
                "events": [{
                    "id": "Step", "track_index": 1, "start_time": 0.25, "end_time": 0.25,
                    "parameters": [{
                        "name": "FootstepEffectsSettings",
                        "property_class": "ObjectProperty",
                        "exported_text": "/Script/Example.Effects'/Game/Effects/Default.Default'",
                    }],
                }],
            },
        }
        relative_clip = "animations/Walk.anim.ron"
        record = notify_record(source, {owner: relative_clip})
        self.assertEqual(record["events"][0]["parameters"], {
            "FootstepEffectsSettings": "None//Game/Effects/Default/Default",
        })
        self.assertEqual(record["sync_markers"], source["animation_timeline"]["sync_markers"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = root / relative_clip
            clip.parent.mkdir()
            clip.write_text("(\n    event_tracks: {},\n)\n", encoding="utf-8")
            metadata = clip.with_name("Walk.notify.json")
            metadata.write_text(json.dumps(record), encoding="utf-8")
            index = clip.with_name("notify_index.json")
            build(root, index, check=False)
            self.assertEqual(json.loads(index.read_bytes())["files"], [metadata.name])
            self.assertIn('StringId("als_notify:/game/animations/walk.walk:step")', clip.read_text())
            self.assertIn("start_time: 0.25", clip.read_text())
            build(root, index, check=True)

            record["events"].clear()
            metadata.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outdated generated asset"):
                build(root, index, check=True)
            build(root, index, check=False)
            self.assertNotIn("event_tracks", clip.read_text())

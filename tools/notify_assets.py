"""Build the notify index and generated Bevy event tracks from adjacent animation metadata."""

import argparse
import json
import os
import re
import struct
import uuid
from collections import defaultdict
from pathlib import Path


EPSILON = 0.0001


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def quoted(value):
    return json.dumps(value, ensure_ascii=False)


def clip_durations(clips):
    durations = {}
    for source in clips:
        if source.get("source_kind") != "AnimSequence":
            continue
        for segment in source.get("runtime_segments", []):
            clip = segment.get("graph_clip")
            if not isinstance(clip, str):
                raise ValueError("invalid_sequence_graph_clip")
            duration = float(source.get("sequence_length", 0.0))
            existing = durations.get(clip)
            if existing is not None and abs(existing - duration) > EPSILON:
                raise ValueError("conflicting_graph_clip_duration:" + clip)
            durations[clip] = duration
    return durations


def segment_for_event(source, event):
    start = float(event["start_time"])
    end = float(event["end_time"])
    matches = []
    for segment in source.get("runtime_segments", []):
        segment_start = float(segment["animation_start_time"])
        segment_end = float(segment["animation_end_time"])
        if start >= f32(segment_start - EPSILON) and end <= f32(segment_end + EPSILON):
            matches.append(segment)
    if len(matches) != 1:
        raise ValueError("event_segment_mapping:" + event["event_id"])
    return matches[0]


def map_time(time, segment):
    return float(segment["start_position"]) + (
        float(time) - float(segment["animation_start_time"])
    ) * float(segment["play_rate"])


def tracks(clips):
    durations = clip_durations(clips)
    result = defaultdict(lambda: defaultdict(list))
    seen_events = set()
    for source in clips:
        for event in source.get("events", []):
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("invalid_event_id")
            if event_id in seen_events:
                raise ValueError("duplicate_event_id:" + event_id)
            seen_events.add(event_id)
            segment = segment_for_event(source, event)
            clip = segment.get("graph_clip")
            if not isinstance(clip, str) or clip not in durations:
                raise ValueError("unresolved_event_graph_clip:" + event_id)
            start = map_time(event["start_time"], segment)
            end = map_time(event["end_time"], segment)
            if start < f32(-EPSILON) or end < f32(start - EPSILON) or end > f32(durations[clip] + EPSILON):
                raise ValueError("compiled_event_time_out_of_range:" + event_id)
            source_track = str(event.get("track_index", -1))
            track_name = "als_notify:" + source["source_asset"].lower() + ":" + source_track
            result[clip][track_name].append(
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, event_id)),
                    "event_id": event_id,
                    "start_time": start,
                    "end_time": max(start, end),
                }
            )
    return result


def render_tracks(track_map):
    lines = ["    event_tracks: {"]
    for name in sorted(track_map):
        lines.append("        " + quoted(name) + ": (")
        lines.append("            name: " + quoted(name) + ",")
        lines.append("            events: [")
        for event in sorted(track_map[name], key=lambda value: (value["start_time"], value["end_time"], value["event_id"])):
            lines.extend(
                [
                    "                (",
                    "                    id: " + quoted(event["id"]) + ",",
                    "                    value: (",
                    "                        event: StringId(" + quoted(event["event_id"]) + "),",
                    "                        start_time: " + repr(event["start_time"]) + ",",
                    "                        end_time: " + repr(event["end_time"]) + ",",
                    "                    ),",
                    "                ),",
                ]
            )
        lines.append("            ],")
        lines.append("        ),")
    lines.append("    },")
    return "\n".join(lines)


def remove_event_tracks(text):
    marker = "\n    event_tracks:"
    start = text.find(marker)
    if start < 0:
        return text
    if text.find(marker, start + len(marker)) >= 0:
        raise ValueError("multiple_event_tracks_fields")
    brace = text.find("{", start)
    if brace < 0:
        raise ValueError("event_tracks_missing_map")
    depth = 0
    end = None
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise ValueError("event_tracks_unclosed_map")
    if end < len(text) and text[end] == ",":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1
    names = re.findall(r'^\s*name: "([^"]+)"', text[start:end], re.MULTILINE)
    if any(not name.startswith("als_notify:") for name in names) or (
        not names and text[brace + 1 : text.rfind("}", brace, end)].strip()
    ):
        raise ValueError("event_tracks also contains authored Bevy tracks")
    return text[:start] + "\n" + text[end:]


def update_graph_clip(path, track_map, check):
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if not track_map and '\n    event_tracks:' not in text:
        return
    if not track_map and 'name: "als_notify:' not in text:
        return
    try:
        text = remove_event_tracks(text)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if track_map:
        end = text.rfind("\n)")
        if end < 0:
            raise ValueError("graph clip missing final delimiter: " + path.as_posix())
        text = text[:end] + "\n" + render_tracks(track_map) + text[end:]
    write(path, text.replace("\n", newline).encode(), check)


def write(path, data, check):
    if not path.is_file() or path.read_bytes() != data:
        if check:
            raise ValueError(f"outdated generated asset: {path}")
        path.write_bytes(data)


def build(assets_root, index, check):
    directory = index.parent
    paths = [*assets_root.rglob("*.notify.json"), *assets_root.rglob("*.montage.json")]
    records = sorted(
        ((file, json.loads(file.read_bytes())) for file in paths),
        key=lambda pair: pair[1]["source_asset"].lower(),
    )
    clips = [clip for _, clip in records]
    if not clips or len({clip["source_asset"].lower() for clip in clips}) != len(clips):
        raise ValueError("expected distinct animation metadata records")
    compiled = tracks(clips)
    for clip in clips:
        for segment in clip["runtime_segments"]:
            path = assets_root / segment["graph_clip"]
            if not path.is_file() or not path.resolve().is_relative_to(assets_root.resolve()):
                raise ValueError(f"missing graph clip within {assets_root}: {path}")
    for path in sorted(assets_root.rglob("*.anim.ron")):
        update_graph_clip(path, compiled.get(path.relative_to(assets_root).as_posix()), check)
    data = {"version": 2, "files": [Path(os.path.relpath(file, directory)).as_posix() for file, _ in records]}
    write(index, (json.dumps(data, indent=2, allow_nan=False) + "\n").encode(), check)
    print(f"{len(records)} notify records, {len(compiled)} graph clips")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True, help="Index path relative to the assets root")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        build(args.assets_root, args.assets_root / args.index, args.check)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()

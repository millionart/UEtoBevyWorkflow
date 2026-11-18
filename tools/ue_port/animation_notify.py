"""Convert an editor animation timeline into metadata beside its exported clip."""

from curves import native_rich_curve
from ue_exported_text import parse_quoted


def fields(rows):
    return {row["name"]: row for row in rows}


def property_value(row):
    text = row["exported_text"]
    if "numeric_value" in row:
        return row["numeric_value"]
    if "enum_value_name" in row:
        enum = row["cpp_type"]
        name = row["enum_value_name"]
        return {"enum": enum, "value": f"{enum}::{name}"}
    if row["property_class"] == "BoolProperty":
        return text == "True"
    if row["property_class"] in {"ByteProperty", "IntProperty", "Int64Property"}:
        return int(text) if text.lstrip("-").isdigit() else text
    if "fields" in row:
        values = {key: property_value(value) for key, value in fields(row["fields"]).items()}
        return {"__tagged__": values}
    text = parse_quoted(text) if text.startswith('"') else text
    if row["property_class"] in {
        "ObjectProperty", "SoftObjectProperty", "ClassProperty", "SoftClassProperty",
    }:
        _, separator, object_path = text.partition("'")
        if separator and object_path.startswith("/") and object_path.endswith("'"):
            package, dot, name = object_path[:-1].rpartition(".")
            if dot:
                # Runtime effect settings use the reference spelling from the installed data.
                return f"None/{package}/{name}"
    return text


def montage_curves(properties):
    raw = fields(properties["RawCurveData"]["fields"])
    curves = {}
    for row in raw["FloatCurves"]["elements"]:
        entries = fields(row["fields"])
        name = entries["CurveName"]["exported_text"]
        keys, pre, post = native_rich_curve(entries["FloatCurve"])
        curves[name] = {
            "times": [key["t"] for key in keys],
            "values": [key["v"] for key in keys],
            "interps": [key["interpolation"] for key in keys],
            "tangent_modes": [
                ("Auto", "User", "Break", "None", "SmartAuto").index(key["tangent_mode"])
                for key in keys
            ],
            "weight_modes": [
                ("None", "Arrive", "Leave", "Both").index(key["tangent_weight_mode"])
                for key in keys
            ],
            **{name + "s": [key[name] for key in keys] for name in (
                "arrive_tangent", "leave_tangent", "arrive_weight", "leave_weight"
            )},
            "pre_infinity": pre.removeprefix("RCCE_"),
            "post_infinity": post.removeprefix("RCCE_"),
        }
    return curves


def notify_record(source, clips):
    owner = source["asset_path"]
    kind = source["asset_class"].rsplit(".", 1)[-1]
    if kind not in {"AnimSequence", "AnimMontage"}:
        raise ValueError(f"expected an animation sequence or montage: {owner}")
    timeline = source["animation_timeline"]
    duration = timeline["sequence_length"]
    result = {
        "source_asset": owner,
        "source_kind": kind,
        "sequence_length": duration,
        "sync_markers": timeline["sync_markers"],
        "events": [
            {
                **event,
                "event_id": f"als_notify:{owner.lower()}:{event['id'].lower()}",
                "parameters": {prop["name"]: property_value(prop) for prop in event["parameters"]},
            }
            for event in timeline["events"]
        ],
    }
    if kind == "AnimSequence":
        result["runtime_segments"] = [{
            "graph_clip": clips[owner], "slot": None, "start_position": 0,
            "animation_start_time": 0, "animation_end_time": duration,
            "play_rate": 1, "loop_count": 1,
        }]
        return result

    segments = timeline["segments"]
    if not segments:
        raise ValueError(f"montage has no animation segments: {owner}")
    result["runtime_segments"] = [
        {
            **{key: value for key, value in segment.items() if key != "source_sequence"},
            "graph_clip": clips[segment["source_sequence"]],
        }
        for segment in segments
    ]
    properties = fields(source["asset_properties"])
    blend_in = fields(properties["BlendIn"]["fields"])
    blend_out = fields(properties["BlendOut"]["fields"])
    result["playback"] = {
        "source_sequence": segments[0]["source_sequence"],
        "play_rate": properties["RateScale"]["numeric_value"],
        "blend_in_seconds": blend_in["BlendTime"]["numeric_value"],
        "blend_out_seconds": blend_out["BlendTime"]["numeric_value"],
        "blend_in_mode": blend_in["BlendOption"]["exported_text"],
        "blend_out_mode": blend_out["BlendOption"]["exported_text"],
        "montage_curves": montage_curves(properties),
    }
    return result

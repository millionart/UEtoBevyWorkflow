import math

from ue_exported_text import field as struct_field, parse_number, reject_unknown_fields

RICH_CURVE_INTERPOLATIONS = {
    "RCIM_Constant": "Constant",
    "RCIM_Linear": "Linear",
    "RCIM_Cubic": "Cubic",
}
RICH_CURVE_TANGENT_MODES = {
    "RCTM_Auto": "Auto",
    "RCTM_User": "User",
    "RCTM_Break": "Break",
    "RCTM_None": "None",
    "RCTM_SmartAuto": "SmartAuto",
}
RICH_CURVE_TANGENT_WEIGHT_MODES = {
    "RCTWM_WeightedNone": "None",
    "RCTWM_WeightedArrive": "Arrive",
    "RCTWM_WeightedLeave": "Leave",
    "RCTWM_WeightedBoth": "Both",
}


class AssetResolutionError(ValueError):
    pass


def rich_curve_enum(fields, name, default, values):
    value = struct_field(fields, name, default)
    target = values.get(value)
    if target is None:
        raise AssetResolutionError(f"source rich curve {name} is unsupported")
    return target


RICH_CURVE_KEY_NUMBERS = {
    "t": "Time",
    "v": "Value",
    "arrive_tangent": "ArriveTangent",
    "arrive_weight": "ArriveTangentWeight",
    "leave_tangent": "LeaveTangent",
    "leave_weight": "LeaveTangentWeight",
}


def rich_curve_key_fields(fields):
    reject_unknown_fields(fields, {"InterpMode", "TangentMode", "TangentWeightMode", *RICH_CURVE_KEY_NUMBERS.values()})
    result = {
        "interpolation": rich_curve_enum(fields, "InterpMode", "RCIM_Linear", RICH_CURVE_INTERPOLATIONS),
        "tangent_mode": rich_curve_enum(fields, "TangentMode", "RCTM_Auto", RICH_CURVE_TANGENT_MODES),
        "tangent_weight_mode": rich_curve_enum(fields, "TangentWeightMode", "RCTWM_WeightedNone", RICH_CURVE_TANGENT_WEIGHT_MODES),
    }
    for name, field_name in RICH_CURVE_KEY_NUMBERS.items():
        value = struct_field(fields, field_name, "0.0")
        if type(value) in (float, int) and math.isfinite(value):
            result[name] = float(value)
        else:
            result[name] = parse_number(value)
    return result


def native_rich_curve(curve):
    try:
        fields = {row["name"]: row for row in curve["fields"]}
        if not math.isfinite(fields["DefaultValue"]["numeric_value"]):
            raise ValueError("curve default is invalid")
        pre = fields["PreInfinityExtrap"]["exported_text"]
        post = fields["PostInfinityExtrap"]["exported_text"]
        keys = []
        for element in fields["Keys"]["elements"]:
            key = {row["name"]: row for row in element["fields"]}
            values = {name: key[name]["numeric_value"] for name in RICH_CURVE_KEY_NUMBERS.values()}
            if any(type(value) not in (int, float) for value in values.values()):
                raise TypeError("curve key number is invalid")
            for name in ("InterpMode", "TangentMode", "TangentWeightMode"):
                values[name] = key[name]["exported_text"]
            keys.append(rich_curve_key_fields(values))
    except (KeyError, TypeError, ValueError) as error:
        raise AssetResolutionError("source CurveFloat native fields are invalid") from error
    if not keys or any(right["t"] <= left["t"] for left, right in zip(keys, keys[1:])):
        raise AssetResolutionError("source CurveFloat key times are invalid")
    return keys, pre, post


def convert_curve(source):
    asset_class = source.get('asset_class')
    properties = source['asset_properties']
    if asset_class == '/Script/Engine.CurveFloat':
        axis_names, property_name = ('value',), 'FloatCurve'
    elif asset_class in {'/Script/Engine.CurveVector', '/Script/Engine.CurveLinearColor'}:
        axis_names = ('x', 'y', 'z') if asset_class.endswith('CurveVector') else ('r', 'g', 'b', 'a')
        property_name = 'FloatCurves'
    else:
        raise AssetResolutionError('expected a float, vector or linear color curve')
    channels = sorted((row for row in properties if row['name'] == property_name), key=lambda row: row['array_index'])
    if [row['array_index'] for row in channels] != list(range(len(axis_names))):
        raise AssetResolutionError('curve channel indices are missing or ambiguous')
    result = {}
    for axis, curve in zip(axis_names, channels):
        keys, pre, post = native_rich_curve(curve)
        default = next(row['numeric_value'] for row in curve['fields'] if row['name'] == 'DefaultValue')
        result[axis] = {'keys': keys, 'default': default, 'pre_extrapolation': pre, 'post_extrapolation': post}
    return {'format': 'bevy_port_curve_v1', 'source_asset': source['asset_path'], 'channels': result}

import re


from ue_exported_text import (
    ExportedTextError,
    field,
    parse_bool,
    parse_list,
    parse_number,
    parse_quoted,
    parse_scale_bias,
    parse_scale_bias_clamp,
    parse_struct,
    reject_unknown_fields,
)


SYNC_GROUP_ROLES = {
    "CanBeLeader",
    "AlwaysFollower",
    "AlwaysLeader",
    "TransitionLeader",
    "TransitionFollower",
    "ExclusiveAlwaysLeader",
}


class PoseContractError(ValueError):
    pass


def source_node_id(node):
    value = node.get("source_node", node.get("object_path"))
    if not isinstance(value, str) or not value:
        raise PoseContractError("source node identity is invalid")
    return value


def node_property(node, name):
    values = [
        value.get("exported_text")
        for value in node.get("properties", [])
        if value.get("name") == name and isinstance(value.get("exported_text"), str)
    ]
    if len(values) != 1:
        raise PoseContractError(f"source node property is missing or ambiguous: {name}")
    return values[0]


def node_struct_fields(node, name):
    fields = parse_struct(node_property(node, name))
    record = next(value for value in node["properties"] if value.get("name") == name)
    for value in record.get("fields", []):
        if int(value.get("flags", "0"), 0) & 1:
            text = value["exported_text"]
            if not text and value.get("property_class") in {"ArrayProperty", "SetProperty", "MapProperty"}:
                text = "()"
            fields.setdefault(value["name"], text)
    return fields


def visible_input_names(node):
    return [
        pin.get("name")
        for pin in node.get("pins", [])
        if pin.get("direction") == "Input" and not pin.get("hidden")
    ]


def visible_output_names(node):
    return [
        pin.get("name")
        for pin in node.get("pins", [])
        if pin.get("direction") == "Output" and not pin.get("hidden")
    ]


def visible_input_default(node, name, default=None, parser=parse_number):
    pins = [
        pin
        for pin in node.get("pins", [])
        if pin.get("direction") == "Input" and not pin.get("hidden") and pin.get("name") == name
    ]
    if not pins and default is not None:
        return default
    if len(pins) != 1:
        raise PoseContractError(f"source input pin is missing or ambiguous: {name}")
    if default is not None and (
        pins[0].get("default_value_ignored")
        or input_is_linked(node, name)
        or name in property_binding_paths(node)
    ):
        return default
    return parser(pins[0].get("default_value"))


def property_binding_paths(node):
    binding = node.get("binding")
    if binding is None:
        return {}
    if not isinstance(binding, dict):
        raise PoseContractError("source property binding is invalid")
    values = [
        value.get("exported_text")
        for value in binding.get("properties", [])
        if value.get("name") == "PropertyBindings" and isinstance(value.get("exported_text"), str)
    ]
    if len(values) != 1:
        raise PoseContractError("source property bindings are missing or ambiguous")
    result = {}
    for item in parse_list(values[0]):
        pair = parse_list(item)
        if len(pair) != 2:
            raise PoseContractError("source property binding entry is invalid")
        pin = parse_quoted(pair[0])
        fields = parse_struct(pair[1])
        if parse_quoted(field(fields, "PropertyName")) != pin:
            raise PoseContractError("source property binding pin name is invalid")
        if not parse_bool(field(fields, "bIsBound")):
            continue
        path = ".".join(parse_quoted(part) for part in parse_list(field(fields, "PropertyPath")))
        if path.startswith("GetParent."):
            path = path[len("GetParent.") :]
        if not path or pin in result:
            raise PoseContractError("source property binding path is invalid")
        result[pin] = path
    return result


def input_is_linked(node, name):
    pins = [
        pin
        for pin in node.get("pins", [])
        if pin.get("direction") == "Input" and not pin.get("hidden") and pin.get("name") == name
    ]
    if len(pins) != 1:
        raise PoseContractError("source input pin is missing or ambiguous")
    return any(item.get("node_object_path") != "None" for item in pins[0].get("linked_to", []))


def issue(node, renderer, kind, detail):
    return {
        "kind": kind,
        "source_node": source_node_id(node),
        "source_class": node.get("source_class", node.get("class_path")),
        "renderer": renderer,
        "detail": detail,
    }


def parse_name(value):
    if value.startswith('"'):
        return parse_quoted(value)
    if not value:
        raise ExportedTextError("name value is invalid")
    return value


def validate_inputs(node, renderer, required, optional=()):
    names = visible_input_names(node)
    missing = [name for name in required if name not in names]
    extra = [name for name in names if name not in set(required) | set(optional)]
    issues = []
    if missing:
        issues.append(issue(node, renderer, "source_pin_missing", ",".join(missing)))
    if extra:
        issues.append(issue(node, renderer, "source_pin_unsupported", ",".join(extra)))
    return names, issues


def lifecycle_issues(node, renderer, fields, names):
    issues = []
    for name in names:
        if field(fields, name, "()") != "()":
            issues.append(issue(node, renderer, "source_lifecycle_unsupported", name))
    return issues


def sequence_player_contract(node):
    renderer = "sequence_player"
    fields = node_struct_fields(node, "Node")
    reject_unknown_fields(
        fields,
        {
            "GroupName",
            "GroupRole",
            "Method",
            "Sequence",
            "PlayRateBasis",
            "PlayRate",
            "PlayRateScaleBiasClampConstants",
            "PlayRateScaleBiasClampState",
            "StartPosition",
            "bLoopAnimation",
            "bStartFromMatchingPose",
            "bIgnoreForRelevancyTest",
            "bOverridePositionWhenJoiningSyncGroupAsLeader",
            "InitialUpdateFunction",
            "BecomeRelevantFunction",
            "UpdateFunction",
        },
    )
    names, issues = validate_inputs(node, renderer, (), ("PlayRate", "StartPosition", "bLoopAnimation"))
    issues.extend(
        lifecycle_issues(
            node,
            renderer,
            fields,
            ("InitialUpdateFunction", "BecomeRelevantFunction", "UpdateFunction"),
        )
    )
    group = parse_name(field(fields, "GroupName", "None"))
    group_role = parse_name(field(fields, "GroupRole", "CanBeLeader"))
    method = parse_name(field(fields, "Method", "DoNotSync"))
    if group_role not in SYNC_GROUP_ROLES:
        issues.append(issue(node, renderer, "source_sync_group_role_unsupported", group_role))
    if group != "None" or method != "DoNotSync":
        issues.append(issue(node, renderer, "source_sync_group_unsupported", f"{group}:{method}"))
    if parse_bool(field(fields, "bStartFromMatchingPose", "False")):
        issues.append(issue(node, renderer, "source_pose_matching_unsupported", "bStartFromMatchingPose"))
    if field(fields, "PlayRateScaleBiasClampState", "()") != "()":
        issues.append(issue(node, renderer, "source_player_runtime_state_unsupported", "PlayRateScaleBiasClampState"))
    bindings = property_binding_paths(node)
    play_rate_path = bindings.get("PlayRate", "")
    loop_animation_path = bindings.get("bLoopAnimation", "")
    if play_rate_path and input_is_linked(node, "PlayRate"):
        issues.append(issue(node, renderer, "source_property_binding_link_unsupported", "PlayRate"))
    if loop_animation_path and "bLoopAnimation" in names and input_is_linked(node, "bLoopAnimation"):
        issues.append(issue(node, renderer, "source_property_binding_link_unsupported", "bLoopAnimation"))
    play_rate = visible_input_default(node, "PlayRate", parse_number(field(fields, "PlayRate", "1.0")))
    return {
        "renderer": renderer,
        "input_pins": names,
        "sequence": parse_quoted(field(fields, "Sequence")),
        "play_rate_basis": parse_number(field(fields, "PlayRateBasis", "1.0")),
        "play_rate": play_rate,
        "play_rate_path": play_rate_path,
        "play_rate_pin": "PlayRate",
        "play_rate_mapping": parse_scale_bias_clamp(
            field(fields, "PlayRateScaleBiasClampConstants", "()")
        ),
        "start_position": visible_input_default(node, "StartPosition", parse_number(field(fields, "StartPosition", "0.0"))),
        "start_position_pin": "StartPosition",
        "loop_animation": visible_input_default(node, "bLoopAnimation", parse_bool(field(fields, "bLoopAnimation", "True")), parse_bool),
        "ignore_for_relevancy_test": parse_bool(field(fields, "bIgnoreForRelevancyTest", "False")),
        "loop_animation_path": loop_animation_path,
        "loop_animation_pin": "bLoopAnimation",
        "sync_group": group,
        "sync_group_role": group_role,
        "sync_method": method,
        "override_position_when_joining_sync_group_as_leader": parse_bool(
            field(fields, "bOverridePositionWhenJoiningSyncGroupAsLeader", "False")
        ),
    }, issues


def generated_anim_blueprint_asset(value):
    if value in {"", "None"}:
        return ""
    parsed = parse_quoted(value)
    match = re.fullmatch(r"[^']*'(/[^']+)'", parsed)
    if match is None:
        raise PoseContractError("linked animation generated class is invalid")
    class_path = match.group(1)
    package, separator, generated_name = class_path.rpartition(".")
    if not separator or not generated_name.endswith("_C"):
        raise PoseContractError("linked animation generated class is invalid")
    asset_name = generated_name.removesuffix("_C")
    if package.rsplit("/", 1)[-1] != asset_name:
        raise PoseContractError("linked animation generated class is invalid")
    return package + "." + asset_name


def linked_anim_layer_contract(node):
    renderer = "linked_anim_layer"
    fields = parse_struct(node_property(node, "Node"))
    reject_unknown_fields(
        fields,
        {
            "Interface",
            "Layer",
            "InstanceClass",
            "InitialUpdateFunction",
            "BecomeRelevantFunction",
            "UpdateFunction",
        },
    )
    inputs, issues = validate_inputs(node, renderer, ())
    outputs = visible_output_names(node)
    if outputs != ["Pose"]:
        issues.append(issue(node, renderer, "source_pin_unsupported", "Output"))
    interface = parse_quoted(field(fields, "Interface"))
    match = re.fullmatch(r"[^']*'(/[^']+)'", interface)
    if match is None:
        raise PoseContractError("linked animation layer interface is invalid")
    layer = parse_quoted(field(fields, "Layer"))
    if not layer:
        raise PoseContractError("linked animation layer name is invalid")
    issues.extend(
        lifecycle_issues(
            node,
            renderer,
            fields,
            ("InitialUpdateFunction", "BecomeRelevantFunction", "UpdateFunction"),
        )
    )
    return {
        "renderer": renderer,
        "input_pins": inputs,
        "output_pins": outputs,
        "interface": match.group(1),
        "layer": layer,
        "instance_class": generated_anim_blueprint_asset(field(fields, "InstanceClass", "None")),
    }, issues

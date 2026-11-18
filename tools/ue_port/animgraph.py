import json
import math
import re
from pathlib import PurePosixPath
from uuid import UUID

from graph_contract import contract
from pose_rules import input_is_linked, sequence_player_contract
from reachability import traverse
from ue_exported_text import parse_scale_bias_clamp

ROOT_NODE = '/Script/AnimGraph.AnimGraphNode_Root'
SEQUENCE_NODE = '/Script/AnimGraph.AnimGraphNode_SequencePlayer'


def graph_plan(source, root):
    if not source['asset_class'].endswith('.AnimBlueprint'):
        raise ValueError('expected an animation blueprint')
    reachability = traverse(source, root)
    return {
        'format': 'bevy_port_animgraph_plan_v1',
        'reachability': reachability,
        'contracts': [contract(source, g['source_graph']) for g in reachability['graphs']],
    }


def single_clip_graph(plan, clips):
    reachability = plan['reachability']
    if (reachability['issues'] or reachability['external_dependencies'] or reachability['function_dependencies']
            or len(plan['contracts']) != 1):
        raise ValueError('graph requires project adapters; inspect the graph plan')
    graph, = plan['contracts']
    nodes = {node['object_path']: node for node in graph['source_graph']['nodes']}
    root_nodes = [node for node in nodes.values() if node['class_path'] == ROOT_NODE]
    if len(root_nodes) != 1:
        raise ValueError('graph must have exactly one root')
    output = root_nodes[0]
    incoming = [edge for edge in graph['links'] if edge['target']['node_object_path'] == output['object_path']]
    if len(incoming) != 1 or incoming[0]['source']['pin_name'] != 'Pose' or incoming[0]['target']['pin_name'] != 'Result':
        raise ValueError('root must consume exactly one pose output')
    player = nodes[incoming[0]['source']['node_object_path']]
    if player['class_path'] != SEQUENCE_NODE:
        raise ValueError('stock graph compiler does not implement: ' + player['class_path'])
    settings, issues = sequence_player_contract(player)
    if (issues or player['binding'] or any(input_is_linked(player, name) for name in settings['input_pins'])
            or settings['play_rate'] != 1.0 or settings['play_rate_basis'] != 1.0
            or settings['start_position'] != 0.0 or settings['loop_animation']
            or settings['play_rate_mapping'] != parse_scale_bias_clamp('()')):
        raise ValueError('sequence needs explicit rate, start, loop, binding or lifecycle handling')
    ref = re.fullmatch(r"[^']*'(/[^']+)'", settings['sequence'])
    if ref is None:
        raise ValueError('sequence object reference is invalid')
    clip = clips.get(ref[1])
    if not isinstance(clip, str) or not clip:
        raise ValueError('missing target clip mapping: ' + ref[1])
    path = PurePosixPath(clip.split('#',1)[0])
    if path.is_absolute() or '..' in path.parts or '\\' in clip:
        raise ValueError('clip path must be relative to the Bevy asset root')
    node_id = json.dumps(str(UUID(player['node_guid'])))
    name = json.dumps(player['title'], ensure_ascii=False)
    clip = json.dumps(clip, ensure_ascii=False)
    pos = [float(p.get(f'position_{axis}', 0)) for p in (player, output) for axis in ('x', 'y')]
    if not all(map(math.isfinite,pos)):
        raise ValueError('node positions must be finite')
    x, y, ox, oy = pos
    return f'''(
    nodes: [(id: {node_id}, name: {name}, inner: {{
        "bevy_animation_graph::builtin_nodes::ClipNode": (
            clip: {clip}, override_duration: None, override_interpolation: None,
            root_motion_mode: Disabled, root_motion_bone: None,
        ),
    }}, should_debug: false)],
    edges_inverted: {{OutputData("pose"): NodeData({node_id}, "pose"), OutputTime: NodeTime({node_id})}},
    io_spec: (input_times: [], input_data: {{}}, output_time: true, output_data: {{"pose": Pose}}, input_order: {{}}, output_order: {{Data("pose"): 0}}),
    default_data: {{}},
    editor_metadata: (node_positions: {{{node_id}: ({x}, {y})}}, input_position: (0.0, 0.0), output_position: ({ox}, {oy})),
)
'''

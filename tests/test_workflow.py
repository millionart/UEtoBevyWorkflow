import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from port import asset_from_file, run
from animgraph import graph_plan, single_clip_graph
from curves import convert_curve
from ue_control_rig_target import compile_target, definition_ron

CLIP = '/Game/Animation/A_Move.A_Move'


def clip_blueprint():
    asset = '/Game/Animation/AB_Demo.AB_Demo'
    root = asset + ':AnimGraph'
    output = {'object_path': root+'.Root', 'node_guid': '1f46f1a9-1be6-48ba-adc5-76186104b02c',
              'class_path': '/Script/AnimGraph.AnimGraphNode_Root', 'title': 'Result',
              'position_x': 400, 'position_y': 20}
    player = {'object_path': root+'.Clip', 'node_guid': '3ba63502-4cfd-4ef2-8a5a-780962678af9',
              'class_path': '/Script/AnimGraph.AnimGraphNode_SequencePlayer', 'title': 'Move',
              'position_x': 10, 'position_y': 20, 'properties': [{'name': 'Node',
              'exported_text': f'''(Sequence="/Script/Engine.AnimSequence'{CLIP}'",bLoopAnimation=False)'''}]}
    output['pins'] = [{'name': 'Result', 'direction': 'Input', 'pin_id': 'result',
                       'linked_to': [{'node_object_path': player['object_path'], 'node_guid': player['node_guid'],
                                      'direction':'Output', 'pin_id': 'pose', 'pin_name': 'Pose'}]}]
    player['pins'] = [{'name': 'Pose', 'direction': 'Output', 'pin_id': 'pose',
                       'linked_to': [{'node_object_path': output['object_path'], 'node_guid': output['node_guid'],
                                      'direction': 'Input', 'pin_id': 'result', 'pin_name': 'Result'}]}]
    return {'format': 'ue_animation_blueprint_graph_v1', 'asset_class': '/Script/Engine.AnimBlueprint',
            'asset_path': asset, 'asset_objects': [], 'graphs': [{'name': 'AnimGraph', 'object_path': root,
                'class_path': '/Script/AnimGraph.AnimationGraph', 'nodes': [output,player]}]}


def curve_record():
    def key(time, value):
        fields = [{'name': name, 'numeric_value': number} for name, number in (
            ('Time',time), ('Value',value), ('ArriveTangent',2.1), ('LeaveTangent',3),
            ('ArriveTangentWeight',0), ('LeaveTangentWeight',0.2))]
        fields.extend({'name': name, 'exported_text': text} for name, text in (
            ('InterpMode','RCIM_Cubic'),('TangentMode','RCTM_User'),('TangentWeightMode','RCTWM_WeightedLeave')))
        return {'fields': fields}
    return {'format': 'ue_asset_source_v1', 'asset_class': '/Script/Engine.CurveFloat',
        'asset_path': '/Game/Demo/C_Motion.C_Motion', 'asset_properties': [{'name': 'FloatCurve',
          'array_index': 0, 'fields': [{'name': 'Keys', 'elements': [key(0,0.2),key(1,0.4)]},
          {'name': 'DefaultValue', 'numeric_value': 0.0},
          {'name': 'PreInfinityExtrap', 'exported_text': 'RCCE_Constant'},
          {'name': 'PostInfinityExtrap', 'exported_text': 'RCCE_Cycle'}]}]}


def rig_plan():
    transform = {'translation': {'x': 1., 'y': 2., 'z': 3.}, 'rotation': {'x': 0., 'y': 0., 'z': 0., 'w': 1.},
                 'scale': {'x': 1., 'y': 1., 'z': 1.}}
    return {'unit_defaults_complete': True, 'execution_contract_complete': True,
        'program': {'entries': {'Forwards Solve': 0}, 'instructions': [{'opcode':'Exit'}],
                    'memories': {'Literal': [], 'Work': [], 'External': []}},
        'rig_hierarchy': {'element_count': 1, 'elements': [{'type': 'Bone', 'name': 'root'}],
                         'bones': [{'name': 'root', 'parent': 'None', 'initial_local_transform': transform,
                                    'initial_global_transform': transform}]}}


class WorkflowTests(unittest.TestCase):
    def test_graph_preserves_clip_and_layout_and_rejects_loop(self):
        snapshot = clip_blueprint()
        plan = graph_plan(snapshot,'AnimGraph')
        self.assertEqual(len(plan['contracts']),1)
        path = 'animations/move.anim.ron'
        ron = single_clip_graph(plan,{CLIP: path})
        self.assertIn(f'clip: "{path}"',ron)
        self.assertIn('output_position: (400.0, 20.0)',ron)
        with self.assertRaisesRegex(ValueError,'relative'):
            single_clip_graph(plan,{CLIP:'../move.anim.ron'})
        fields = snapshot['graphs'][0]['nodes'][1]['properties'][0]
        fields['exported_text'] = fields['exported_text'].replace('False','True')
        with self.assertRaisesRegex(ValueError,'loop'):
            single_clip_graph(graph_plan(snapshot,'AnimGraph'),{CLIP:path})

    def test_curve_preserves_extrapolation_and_weighted_cubic_data(self):
        snapshot = curve_record()
        curve = convert_curve(snapshot)['channels']['value']
        self.assertEqual(curve['post_extrapolation'],'RCCE_Cycle')
        self.assertEqual(curve['keys'][1]['interpolation'],'Cubic')
        self.assertEqual(curve['keys'][1]['leave_weight'],0.2)
        malformed = copy.deepcopy(snapshot)
        keys = malformed['asset_properties'][0]['fields'][0]['elements']
        keys[1] = copy.deepcopy(keys[0])
        with self.assertRaisesRegex(ValueError,'times'):
            convert_curve(malformed)

    def test_rig_preserves_program_and_explicit_basis(self):
        plan = rig_plan()
        target = compile_target(plan)
        self.assertEqual(target['program']['instructions'],['Exit'])
        self.assertEqual(target['bones'][0]['initial_global']['translation'],[1.,3.,2.])
        self.assertIn('meters_per_unit:', definition_ron(target))
        plan['program']['instructions'][0]['opcode'] = 'Unsupported'
        with self.assertRaisesRegex(ValueError,'unsupported'):
            compile_target(plan)

    def test_files_are_scoped_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            file = root/'Content'/'Demo'/'Mesh.uasset'
            file.parent.mkdir(parents=True)
            file.touch()
            self.assertEqual(asset_from_file(file, None, '/Game'),'/Game/Demo/Mesh.Mesh')
            capture = root/'capture.json'
            capture.write_text(json.dumps(curve_record()))
            output = root/'curve.json'
            args = SimpleNamespace(kind='curve', capture=capture, output=output)
            run(args)
            self.assertIn('channels',json.loads(output.read_text()))
            with self.assertRaisesRegex(ValueError,'exists'):
                run(args)
            self.assertIn('asset_properties',json.loads(capture.read_text()))


if __name__ == '__main__':
    unittest.main()

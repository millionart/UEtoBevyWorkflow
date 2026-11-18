import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'ue_port'))
from animation_glb import animation_glb
from animgraph import graph_plan, single_clip_graph
from curves import convert_curve
from ue_control_rig_contract import compile_contract
from ue_control_rig_target import compile_target, definition_ron

READER = Path(__file__).resolve().with_name('run_ue_source_reader.ps1')


def asset_from_file(path, content_root, mount):
    path = path.resolve(strict=True)
    content_root = content_root or next((p for p in path.parents if p.name.lower() == 'content'), None)
    if content_root is None or path.suffix.lower() != '.uasset' or not re.fullmatch(r'/[A-Za-z0-9_]+', mount):
        raise ValueError('provide a uasset in Content and a package mount such as /Game')
    package = path.relative_to(content_root.resolve(strict=True)).with_suffix('').as_posix()
    return f'{mount}/{package}.{path.stem}'


def capture(asset):
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / 'capture.json'
        command = ['pwsh', '-NoProfile', '-File', str(READER), '-Asset', asset, '-Output', str(output)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=930)
        if process.returncode or not output.is_file():
            detail = process.stderr.strip().splitlines()[-1:] or ['no capture was written']
            raise ValueError(f'editor reader failed ({process.returncode}): {detail[0]}')
        watchdog = json.loads(output.with_suffix('.json.reader.report.json').read_text(encoding='utf-8'))
        data = json.loads(output.read_text(encoding='utf-8'))
        if not watchdog['success'] or data['asset_path'] != asset:
            raise ValueError('reader did not complete or returned a different asset')
        return data


def run(args):
    if args.output.exists():
        raise ValueError('output already exists')
    if args.kind == 'animation':
        if args.gltf is None or args.output.suffix.lower() != '.glb':
            raise ValueError('animation conversion needs --gltf and a .glb output')
        result = animation_glb(args.gltf)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('xb') as output:
            output.write(result)
        return
    if getattr(args, 'gltf', None) is not None:
        raise ValueError('--gltf is only used by the animation command')
    if args.capture:
        data = json.loads(args.capture.read_text(encoding='utf-8'))
    else:
        asset = args.asset or asset_from_file(args.uasset, args.content_root, args.mount)
        data = capture(asset)
    if not isinstance(data, dict) or data.get('format') not in {'ue_asset_source_v1', 'ue_animation_blueprint_graph_v1'}:
        raise ValueError('expected an editor source reader snapshot')
    result = data
    if args.kind in {'graph-plan', 'animgraph'}:
        result = graph_plan(data, args.root_graph)
        if args.kind == 'animgraph':
            if args.clips is None:
                raise ValueError('animgraph conversion needs a target clip map')
            result = single_clip_graph(result, json.loads(args.clips.read_text(encoding='utf-8')))
    elif args.kind == 'curve':
        result = convert_curve(data)
    elif args.kind in {'rig-plan','rig'}:
        result = compile_contract(data)
        if args.kind == 'rig':
            result = compile_target(result)
            if args.output.suffix == '.ron':
                result = definition_ron(result)
    payload = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        output.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['capture', 'graph-plan', 'animgraph', 'animation', 'rig-plan', 'rig', 'curve'])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--asset', help='mounted UE object path ending in .AssetName')
    source.add_argument('--uasset', type=Path, help='asset file inside the selected Content root')
    source.add_argument('--capture', type=Path, help='replay an existing editor snapshot')
    source.add_argument('--gltf', type=Path, help='Unreal animation export (.gltf or .glb)')
    parser.add_argument('--content-root', type=Path)
    parser.add_argument('--mount', default='/Game')
    parser.add_argument('--root-graph', default='AnimGraph')
    parser.add_argument('--clips', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({'success': False, 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'success': True, 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

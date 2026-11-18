# UE to Bevy Workflow

A source-first template for porting parts of an Unreal Engine project to Bevy. It includes a read-only asset reader for UE 5.8, analysis and conversion tools for animation blueprints, Control Rig, and rich curves, and an extensible [workflow skill](skills/ue-to-bevy/SKILL.md).

## Set up a source project

Install Python 3.10+, PowerShell 7, and a working Unreal Editor for the source project. Install the editor's C++ build tools (Xcode on macOS, the Visual Studio C++ tools on Windows). Copy `tools/env.example.json` to `tools/env.local.json` and set the editor executable and source `.uproject`. Enable Control Rig in the source project when inspecting rigs. The first capture builds the reader in the ignored `.work/` cache.

Create a snapshot in `.work/`. The source editor must be able to resolve the package mount and asset name.

```sh
python3 tools/port.py capture --asset /Game/Animation/AB_Example.AB_Example --output .work/blueprint.json
python3 tools/port.py graph-plan --capture .work/blueprint.json --output .work/blueprint.plan.json
```

Use `--uasset <file>` instead of `--asset` for a file in a Content directory. Set `--content-root` for an atypical layout and `--mount` for plugin content. `--capture` replays an existing reader snapshot. A converter refuses to replace an existing file.

## Available stages

| Command | Output and scope |
| --- | --- |
| `capture` | Read-only snapshot of asset, compiled defaults, graphs, pins, links, layout, and supported typed properties. |
| `graph-plan` | Reachability from the AnimBP root, including states, transition graphs, cache producers, callbacks, and external dependencies. Captures for referenced packages are separate. |
| `animgraph` | Bevy Animation Graph RON for a single unlinked, non-looping Sequence Player with unit rate, zero start, no sync group, and a caller-supplied clip mapping. Other graph shapes stay plans until a target adapter covers their semantics. |
| `rig-plan` | Complete authored graph plus compiled RigVM and hierarchy contract. |
| `rig` | Typed program and hierarchy for `bevy_control_rig` as JSON or, for `.ron` output, a RigDefinition RON value. Unmapped unit types, unresolved coordinates or dimensions, and unsupported instructions fail. |
| `curve` | Rich curve channels with key times, values, interpolation, tangents and weights, defaults, and extrapolation. Values retain the declared input units. |

For a stock clip graph, author a JSON object mapping the full source animation object path to a relative Bevy asset path. Supply a target clip with the matching skeleton, rate, and root motion import policy.

```json
{"/Game/Animation/A_Example.A_Example":"animations/example.anim.ron"}
```

```sh
python3 tools/port.py animgraph --capture .work/blueprint.json --clips .work/clips.json --output .work/example.animgraph.ron
python3 tools/port.py rig-plan --asset /Game/Animation/CR_Example.CR_Example --output .work/rig.plan.json
python3 tools/port.py rig --asset /Game/Animation/CR_Example.CR_Example --output .work/rig.ron
python3 tools/port.py curve --asset /Game/Animation/C_Example.C_Example --output .work/curve.json
```

The RON schema follows the Bevy 0.20 implementation of [Bevy Animation Graph](https://github.com/millionart/bevy_animation_graph/tree/6d706f61c35a6cf7b1c96430a56ae46241b4d51f) and [Bevy Control Rig](https://github.com/millionart/bevy_control_rig/tree/ccca1b0dc7e855a63c6654430b41d3bedc214d3a). Pin the consumer versions and validate generated assets in their real Bevy entry point. Use the plan to drive target-specific nodes and providers; keep those in the consuming project. Add new converter modules for future domains once their source contracts and target consumers are known.

## Verify the template

```sh
python3 -B -m unittest discover -s tests -v
python3 -B tools/ue_port/reachability.py --self-test
python3 -B tools/ue_port/graph_contract.py --self-test
pwsh -NoProfile -File tools/run_ue_source_reader.ps1 -SelfTest
```

Native extraction was exercised on macOS with UE 5.8 for an animation blueprint, a Control Rig, and a float curve. A generated clip graph was loaded with Bevy Animation Graph. The fixture cases use authored test inputs. The project source, runtime assets, capture outputs, and local editor configuration belong to the consuming project. License notices are retained in [LICENSE](LICENSE).

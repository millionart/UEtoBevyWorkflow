# UE to Bevy template

Use the [UE-to-Bevy skill](skills/ue-to-bevy/SKILL.md) for porting or extending conversion coverage. Keep source reads and deterministic conversion separate from the Bevy runtime. Put project classes, paths, renderer adapters, and assets in the consuming project. Check this template with the commands in [README.md](README.md).

Build or clean within each project's own workspace. External engine checkouts are read-only unless the user explicitly authorizes engine changes. Use a local ignored editor configuration, and keep captures and build caches out of commits. Keep necessary fixture inputs focused and retain applicable license and attribution notices.

using UnrealBuildTool;

public class BevySourceReader : ModuleRules
{
    public BevySourceReader(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "PhysicsCore"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "UnrealEd",
            "Chaos",
            "ImageCore",
            "BlueprintGraph",
            "Kismet",
            "AnimGraph",
            "AnimGraphRuntime",
            "ControlRig",
            "MovieScene",
            "MovieSceneTracks",
            "RigVM",
            "RigVMDeveloper",
            "Json",
            "JsonUtilities",
            "SlateCore",
            "SlateNullRenderer"
        });
    }
}

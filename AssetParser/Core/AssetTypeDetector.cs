using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using System.Reflection;
using System.Threading.Tasks;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;
using UAssetAPI.UnrealTypes;
using UAssetAPI.CustomVersions;
using AssetParser.Core;
using AssetParser.Commands;
using AssetParser.Parsers;
using static AssetParser.Core.Helpers;
using static AssetParser.Core.AssetTypeDetector;
using static AssetParser.Core.AssetRefHelper;
using static AssetParser.Parsers.ControlFlowAnalyzer;
using static AssetParser.Parsers.BytecodeAnalyzer;
using static AssetParser.Commands.SummaryCommand;
using static AssetParser.Commands.InspectCommand;
using static AssetParser.Commands.WidgetCommand;
using static AssetParser.Commands.DataTableCommand;
using static AssetParser.Commands.BlueprintCommand;
using static AssetParser.Commands.GraphCommand;
using static AssetParser.Commands.BytecodeCommand;
using static AssetParser.Commands.MaterialCommand;
using static AssetParser.Commands.MaterialFunctionCommand;
using static AssetParser.Commands.ReferencesCommand;
using static AssetParser.Commands.BatchCommands;
using static AssetParser.Commands.BatchBlueprintCommand;
using static AssetParser.Commands.BatchWidgetCommand;
using static AssetParser.Commands.BatchMaterialCommand;
using static AssetParser.Commands.BatchDataTableCommand;

namespace AssetParser.Core
{
    public static class AssetTypeDetector
    {
        
        // Layer 1: Naming convention prefixes (Epic's recommended conventions)
        // https://dev.epicgames.com/documentation/en-us/unreal-engine/recommended-asset-naming-conventions-in-unreal-engine-projects
        public static Dictionary<string, string> NamingPrefixes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            // Blueprints
            ["BP_"] = "Blueprint",
            ["B_"] = "Blueprint",        // Lyra/UE variant
            ["ABP_"] = "Blueprint",      // Animation Blueprint
            ["BPI_"] = "Blueprint",      // Blueprint Interface
            ["GA_"] = "Blueprint",       // Gameplay Ability
            ["GE_"] = "Blueprint",       // Gameplay Effect
            ["GCN_"] = "Blueprint",      // Gameplay Cue Notify
            ["GCNL_"] = "Blueprint",     // Gameplay Cue Notify Looping
            ["WBP_"] = "WidgetBlueprint",
            ["W_"] = "WidgetBlueprint",  // Lyra/UE variant
            ["AC_"] = "Blueprint",       // Actor Component
        
            // Data
            ["DT_"] = "DataTable",
            ["CT_"] = "DataTable",       // Curve Table
            ["C_"] = "Curve",            // Curve assets (CurveFloat/CurveLinearColor/etc.)
            ["DA_"] = "DataAsset",
            ["INPUTDATA_"] = "DataAsset",
            // Project-specific prefixes (TEAMDA_, CFX_, etc.) are loaded via --type-config
            ["ENUM_"] = "Enum",
            ["E_"] = "Enum",
            ["F_"] = "Struct",
        
            // Materials
            ["M_"] = "Material",
            ["MI_"] = "MaterialInstance",
            ["MT_"] = "MaterialInstance",
            ["MF_"] = "MaterialFunction",
            ["MPC_"] = "MaterialParameterCollection",
            ["PPM_"] = "Material",       // Post Process Material
        
            // Meshes
            ["SM_"] = "StaticMesh",
            ["SK_"] = "SkeletalMesh",
            ["SKM_"] = "SkeletalMesh",
        
            // Textures
            ["T_"] = "Texture",
            ["TC_"] = "Texture",         // Texture Cube
            ["RT_"] = "Texture",         // Render Target
            ["HDR_"] = "Texture",        // HDRI
        
            // Animation
            ["AM_"] = "Animation",       // Montage
            ["AS_"] = "Animation",       // Sequence
            ["BS_"] = "Animation",       // Blend Space
            ["Rig_"] = "Animation",
            ["SKEL_"] = "Animation",     // Skeleton
            ["CR_"] = "Animation",       // Control Rig
        
            // FX
            ["NS_"] = "NiagaraSystem",
            ["NE_"] = "NiagaraEmitter",
            ["FXS_"] = "NiagaraSystem",
            ["FXE_"] = "NiagaraEmitter",
            ["PS_"] = "ParticleSystem",  // Legacy Cascade
        
            // Audio
            ["MS_"] = "Sound",           // Meta Sound
            ["SFX_"] = "Sound",
            ["MX_"] = "Sound",
            ["ATT_"] = "Sound",          // Attenuation presets
            ["SCON_"] = "Sound",         // Concurrency presets
            ["CB_"] = "Sound",           // Modulation control buses
            ["CBM_"] = "Sound",          // Modulation control bus mixes
            ["PP_"] = "Sound",           // Modulation parameter patches
            ["CREV_"] = "Sound",         // Reverb presets
            ["DYN_"] = "Sound",          // Dynamics presets
            ["FLT_"] = "Sound",          // Filter presets
            ["TAP_"] = "Sound",          // Delay presets
            ["IR_"] = "Sound",           // Impulse responses
            ["LIB_"] = "Sound",          // MetaSound libraries
            ["SC_"] = "Sound",           // Sound Cue
            ["SW_"] = "Sound",           // Sound Wave
        
            // UI
            ["UI_"] = "WidgetBlueprint",
            ["HUD_"] = "WidgetBlueprint",
        
            // Sequences
            ["LS_"] = "LevelSequence",
        
            // Physics
            ["PHYS_"] = "PhysicsAsset",
            ["PM_"] = "PhysicsMaterial",
        
            // Input
            ["IA_"] = "InputAction",
            ["IMC_"] = "InputMappingContext",
        };
        
        // Layer 2: Exact class name matching (no substring matching to avoid false positives)
        public static Dictionary<string, string> ExactClassTypes = new Dictionary<string, string>()
        {
            // Blueprints
            ["Blueprint"] = "Blueprint",
            ["BlueprintGeneratedClass"] = "Blueprint",
            ["WidgetBlueprint"] = "WidgetBlueprint",
            ["WidgetBlueprintGeneratedClass"] = "WidgetBlueprint",
            ["AnimBlueprint"] = "Blueprint",
            ["AnimBlueprintGeneratedClass"] = "Blueprint",
        
            // Data
            ["DataTable"] = "DataTable",
            ["CurveTable"] = "DataTable",
            ["CurveFloat"] = "Curve",
            ["CurveLinearColor"] = "Curve",
            ["CurveLinearColorAtlas"] = "Curve",
            ["CurveVector"] = "Curve",
            ["DataAsset"] = "DataAsset",
            ["PrimaryDataAsset"] = "DataAsset",
            ["PrimaryAssetLabel"] = "DataAsset",
            // Project-specific DataAsset subclasses are loaded via --type-config
            ["UserDefinedStruct"] = "Struct",
            ["UserDefinedEnum"] = "Enum",
            ["Font"] = "Font",
            ["FontFace"] = "Font",
        
            // Materials
            ["Material"] = "Material",
            ["MaterialInstanceConstant"] = "MaterialInstance",
            ["MaterialInstanceDynamic"] = "MaterialInstance",
            ["MaterialFunction"] = "MaterialFunction",
            ["MaterialParameterCollection"] = "MaterialParameterCollection",
        
            // Meshes
            ["StaticMesh"] = "StaticMesh",
            ["SkeletalMesh"] = "SkeletalMesh",
        
            // Textures
            ["Texture2D"] = "Texture",
            ["TextureCube"] = "Texture",
            ["TextureRenderTarget2D"] = "Texture",
            ["VolumeTexture"] = "Texture",
            ["MediaTexture"] = "Texture",
        
            // Animation
            ["AnimSequence"] = "Animation",
            ["AnimMontage"] = "Animation",
            ["BlendSpace"] = "Animation",
            ["BlendSpace1D"] = "Animation",
            ["AimOffsetBlendSpace"] = "Animation",
            ["Skeleton"] = "Animation",
            ["ControlRig"] = "Animation",
            ["ControlRigBlueprint"] = "Animation",
        
            // FX
            ["NiagaraSystem"] = "NiagaraSystem",
            ["NiagaraEmitter"] = "NiagaraEmitter",
            ["ParticleSystem"] = "ParticleSystem",
        
            // Audio
            ["SoundWave"] = "Sound",
            ["SoundCue"] = "Sound",
            ["SoundAttenuation"] = "Sound",
            ["SoundConcurrency"] = "Sound",
            ["SoundSubmix"] = "Sound",
            ["ITDSpatializationSourceSettings"] = "Sound",
            ["AudioImpulseResponse"] = "Sound",
            ["SubmixEffectReverbPreset"] = "Sound",
            ["SubmixEffectDynamicsProcessorPreset"] = "Sound",
            ["SubmixEffectFilterPreset"] = "Sound",
            ["SubmixEffectTapDelayPreset"] = "Sound",
            ["SoundControlBus"] = "Sound",
            ["SoundControlBusMix"] = "Sound",
            ["SoundModulationParameter"] = "Sound",
            ["SoundModulationParameterPatch"] = "Sound",
            ["MetaSoundSource"] = "Sound",
            ["SoundClass"] = "Sound",
            ["SoundMix"] = "Sound",
        
            // Levels
            ["Level"] = "Level",
            ["World"] = "World",
            ["LevelSequence"] = "LevelSequence",
        
            // Physics
            ["PhysicsAsset"] = "PhysicsAsset",
            ["PhysicalMaterial"] = "PhysicsMaterial",
        
            // Input
            ["InputAction"] = "InputAction",
            ["InputMappingContext"] = "InputMappingContext",
        };
        
        // Layer 3: Structural indicators (presence of certain export types)
        public static Dictionary<string, string> StructuralIndicators = new Dictionary<string, string>()
        {
            // K2Node exports are a definitive indicator of a Blueprint
            ["K2Node_"] = "Blueprint",
            ["EdGraph"] = "Blueprint",
        
            // Material expressions indicate a Material
            ["MaterialExpression"] = "Material",
            ["MaterialGraph"] = "Material",
        
            // DataTable export
            ["DataTableExport"] = "DataTable",
        };
        
        
    }
}

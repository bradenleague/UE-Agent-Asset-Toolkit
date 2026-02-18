using System.Reflection;
using System.Text.Json;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;
using UAssetAPI.UnrealTypes;

// Asset parser CLI for extracting data from Unreal Engine .uasset files
// Usage: AssetParser.exe <command> <asset_path> [options]
//
// Supported asset types:
//   - Widget Blueprints (UMG)
//   - Blueprints (Actor, Character, etc.)
//   - Data Assets
//   - Data Tables
//   - Materials
//   - Textures (metadata only)
//   - Levels
//   - Any other UObject-based asset

// ============================================================================
// ASSET TYPE DETECTION TABLES
// Multi-layer approach: naming conventions -> exact class match -> structural indicators
// ============================================================================

// Layer 1: Naming convention prefixes (Epic's recommended conventions)
// https://dev.epicgames.com/documentation/en-us/unreal-engine/recommended-asset-naming-conventions-in-unreal-engine-projects
var NamingPrefixes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
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
var ExactClassTypes = new Dictionary<string, string>()
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
var StructuralIndicators = new Dictionary<string, string>()
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

// ============================================================================
// MAIN ENTRY POINT
// ============================================================================

if (args.Length < 2)
{
    Console.WriteLine("Usage: AssetParser.exe <command> <asset_path> [--version UE5_3]");
    Console.WriteLine();
    Console.WriteLine("Commands:");
    Console.WriteLine("  summary <path>    - Quick asset type detection and overview");
    Console.WriteLine("  inspect <path>    - Dump all exports and properties");
    Console.WriteLine("  widgets <path>    - Extract widget tree from Widget Blueprint");
    Console.WriteLine("  datatable <path>  - Extract rows from a DataTable");
    Console.WriteLine("  blueprint <path>  - Extract Blueprint functions and variables");
    Console.WriteLine("  material <path>   - Extract Material/MaterialInstance parameters");
    Console.WriteLine("  references <path> - Extract all asset references (imports)");
    Console.WriteLine();
    Console.WriteLine("Batch Commands (for indexing performance):");
    Console.WriteLine("  batch-summary <list_file>    - Process multiple assets, output JSONL");
    Console.WriteLine("  batch-refs <list_file>       - Extract refs for multiple assets, output JSONL");
    Console.WriteLine("  batch-fast <list_file>       - Ultra-fast header-only parsing (10-100x faster)");
    Console.WriteLine("  batch-blueprint <list_file>  - Batch blueprint parsing, output JSONL");
    Console.WriteLine("  batch-widget <list_file>     - Batch widget parsing, output JSONL");
    Console.WriteLine("  batch-material <list_file>   - Batch material parsing, output JSONL");
    Console.WriteLine("  batch-datatable <list_file>  - Batch datatable parsing, output JSONL");
    Console.WriteLine();
    Console.WriteLine("Options:");
    Console.WriteLine("  --version <ver>          - Engine version (e.g., UE5_3, UE5_4, UE5_7)");
    Console.WriteLine("  --type-config <path>     - JSON file with project-specific type mappings");
    return 1;
}

string command = args[0].ToLower();
string assetPath = args[1];
EngineVersion engineVersion = EngineVersion.VER_UE5_7;

// Parse optional arguments
string? typeConfigPath = null;
for (int i = 2; i < args.Length; i++)
{
    if (args[i] == "--version" && i + 1 < args.Length)
    {
        var versionStr = args[i + 1].ToUpper().Replace(".", "_");
        if (!versionStr.StartsWith("VER_"))
            versionStr = "VER_" + versionStr;
        if (Enum.TryParse<EngineVersion>(versionStr, out var ver))
            engineVersion = ver;
        i++;
    }
    else if (args[i] == "--type-config" && i + 1 < args.Length)
    {
        typeConfigPath = args[i + 1];
        i++;
    }
}

// Merge project profile type config if provided
if (typeConfigPath != null && File.Exists(typeConfigPath))
{
    try
    {
        var configJson = File.ReadAllText(typeConfigPath);
        var config = JsonSerializer.Deserialize<JsonElement>(configJson);

        if (config.TryGetProperty("export_class_reclassify", out var reclassify))
        {
            foreach (var entry in reclassify.EnumerateObject())
            {
                ExactClassTypes[entry.Name] = entry.Value.GetString() ?? "Unknown";
            }
        }

        if (config.TryGetProperty("name_prefixes", out var prefixes))
        {
            foreach (var entry in prefixes.EnumerateObject())
            {
                NamingPrefixes[entry.Name] = entry.Value.GetString() ?? "Unknown";
            }
        }
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"Warning: Failed to load type config from {typeConfigPath}: {ex.Message}");
    }
}

// Top-level asset reference for ResolveObjectRef helper
UAsset? currentAsset = null;

// Handle batch commands separately (they read from a file list)
if (command.StartsWith("batch-"))
{
    var listFile = assetPath; // In batch mode, second arg is the list file
    if (!File.Exists(listFile))
    {
        Console.WriteLine(JsonSerializer.Serialize(new { error = $"List file not found: {listFile}" }));
        return 1;
    }

    var paths = File.ReadAllLines(listFile)
        .Where(line => !string.IsNullOrWhiteSpace(line))
        .ToList();

    switch (command)
    {
        case "batch-summary":
            BatchSummary(paths, engineVersion);
            break;
        case "batch-refs":
            BatchReferences(paths, engineVersion);
            break;
        case "batch-fast":
            BatchFastSummary(paths);
            break;
        case "batch-blueprint":
            BatchBlueprint(paths, engineVersion);
            break;
        case "batch-widget":
            BatchWidget(paths, engineVersion);
            break;
        case "batch-material":
            BatchMaterial(paths, engineVersion);
            break;
        case "batch-datatable":
            BatchDataTable(paths, engineVersion);
            break;
        default:
            Console.WriteLine(JsonSerializer.Serialize(new { error = $"Unknown batch command: {command}" }));
            return 1;
    }
    return 0;
}

// Check if file exists
if (!File.Exists(assetPath))
{
    if (File.Exists(assetPath + ".uasset"))
        assetPath = assetPath + ".uasset";
    else
    {
        Console.WriteLine(JsonSerializer.Serialize(new { error = $"Asset not found: {assetPath}" }));
        return 1;
    }
}

try
{
    var asset = new UAsset(assetPath, engineVersion);
    currentAsset = asset;

    switch (command)
    {
        case "summary":
            SummarizeAsset(asset);
            break;
        case "inspect":
            InspectAsset(asset);
            break;
        case "widgets":
            ExtractWidgets(asset);
            break;
        case "datatable":
            ExtractDataTable(asset);
            break;
        case "blueprint":
            ExtractBlueprint(asset);
            break;
        case "material":
            ExtractMaterial(asset);
            break;
        case "materialfunction":
            ExtractMaterialFunction(asset);
            break;
        case "references":
            ExtractReferences(asset);
            break;
        default:
            Console.WriteLine(JsonSerializer.Serialize(new { error = $"Unknown command: {command}" }));
            return 1;
    }
}
catch (IOException ex) when (ex.Message.Contains("being used by another process"))
{
    // Friendly error for file locked by Unreal Editor
    Console.WriteLine(JsonSerializer.Serialize(new {
        error = "Asset is locked by another process (likely Unreal Editor)",
        hint = "Close the asset in UE Editor, or close the Editor entirely to inspect this file",
        path = assetPath,
        type = "FileLocked"
    }));
    return 1;
}
catch (Exception ex)
{
    var innerMsg = ex.InnerException?.Message ?? "";
    var innerInnerMsg = ex.InnerException?.InnerException?.Message ?? "";
    Console.WriteLine(JsonSerializer.Serialize(new {
        error = ex.Message,
        type = ex.GetType().Name,
        inner_error = innerMsg,
        inner_inner_error = innerInnerMsg,
        stack = ex.StackTrace?.Split('\n').Take(3).ToArray()
    }));
    return 1;
}

return 0;

// ============================================================================
// SUMMARY - Quick asset detection
// ============================================================================
void SummarizeAsset(UAsset asset)
{
    var result = new Dictionary<string, object>
    {
        ["path"] = assetPath,
        ["engine_version"] = engineVersion.ToString(),
        ["exports_count"] = asset.Exports.Count,
        ["imports_count"] = asset.Imports.Count
    };

    // Detect asset type from exports
    var exportClasses = asset.Exports
        .Select(e => e.GetExportClassType()?.ToString() ?? "Unknown")
        .Distinct()
        .ToList();

    result["export_classes"] = exportClasses;

    // Determine primary asset type using multi-layer detection
    string assetType = DetectAssetType(assetPath, exportClasses);
    result["asset_type"] = assetType;

    // Get main export info
    var mainExport = asset.Exports.FirstOrDefault();
    if (mainExport != null)
    {
        result["main_export"] = new Dictionary<string, object>
        {
            ["name"] = mainExport.ObjectName.ToString(),
            ["class"] = mainExport.GetExportClassType()?.ToString() ?? "Unknown",
            ["type"] = mainExport.GetType().Name
        };
    }

    // Suggested command based on type
    result["suggested_command"] = assetType switch
    {
        "WidgetBlueprint" => "widgets",
        "DataTable" => "datatable",
        "Blueprint" => "blueprint",
        "Material" or "MaterialInstance" => "material",
        "MaterialFunction" => "materialfunction",
        _ => "inspect"
    };

    Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
}

string DetectAssetType(string path, List<string> exportClasses)
{
    // Get the asset name from path
    var fileName = Path.GetFileNameWithoutExtension(path);

    // Layer 1: Check naming conventions (fast path)
    var prefixType = DetectAssetTypeFromName(fileName);
    if (prefixType != "Unknown")
        return prefixType;

    // Layer 2: Check exact class name matches
    foreach (var exportClass in exportClasses)
    {
        if (ExactClassTypes.TryGetValue(exportClass, out var type))
            return type;
    }

    // Layer 3: Check structural indicators (prefix matching for K2Node_, etc.)
    foreach (var exportClass in exportClasses)
    {
        foreach (var (indicator, type) in StructuralIndicators)
        {
            if (exportClass.StartsWith(indicator))
                return type;
        }
    }

    // Layer 4: Fallback heuristics for less common types
    // Check for specific patterns that didn't match above
    if (exportClasses.Any(c => c.EndsWith("GeneratedClass")))
        return "Blueprint";  // Some kind of blueprint-derived asset

    return "Unknown";
}

// ============================================================================
// INSPECT - Generic full dump
// ============================================================================
void InspectAsset(UAsset asset)
{
    var result = new Dictionary<string, object>
    {
        ["path"] = assetPath,
        ["exports_count"] = asset.Exports.Count,
        ["exports"] = new List<object>()
    };

    foreach (var export in asset.Exports)
    {
        var exportInfo = new Dictionary<string, object>
        {
            ["name"] = export.ObjectName.ToString(),
            ["type"] = export.GetType().Name,
            ["class"] = export.GetExportClassType()?.ToString() ?? "Unknown"
        };

        // Handle different export types
        switch (export)
        {
            case DataTableExport dtExport:
                exportInfo["table_info"] = new Dictionary<string, object>
                {
                    ["row_count"] = dtExport.Table?.Data?.Count ?? 0,
                    ["struct_type"] = dtExport.Table?.Data?.FirstOrDefault()?.StructType?.ToString() ?? "Unknown"
                };
                break;

            case ClassExport classExport:
                exportInfo["class_info"] = new Dictionary<string, object>
                {
                    ["super_struct"] = classExport.SuperStruct?.ToString() ?? "None",
                    ["class_flags"] = classExport.ClassFlags.ToString()
                };
                break;

            case FunctionExport funcExport:
                exportInfo["function_info"] = new Dictionary<string, object>
                {
                    ["function_flags"] = funcExport.FunctionFlags.ToString(),
                    ["has_bytecode"] = funcExport.ScriptBytecode != null
                };
                break;

            case NormalExport normalExport when normalExport.Data != null:
                var props = new List<object>();
                foreach (var prop in normalExport.Data)
                {
                    props.Add(new Dictionary<string, object>
                    {
                        ["name"] = prop.Name.ToString(),
                        ["type"] = prop.PropertyType.ToString(),
                        ["value"] = GetPropertyValue(prop, 0)
                    });
                }
                exportInfo["properties"] = props;
                break;
        }

        ((List<object>)result["exports"]).Add(exportInfo);
    }

    Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
}

// ============================================================================
// WIDGETS - Widget Blueprint extraction as XML
// ============================================================================
void ExtractWidgets(UAsset asset)
{
    var xml = new System.Text.StringBuilder();
    xml.AppendLine("<widget-blueprint>");

    // Extract blueprint metadata (parent class, interfaces, events, variables)
    var classExport = asset.Exports.OfType<ClassExport>().FirstOrDefault();
    var bpExport = asset.Exports
        .OfType<NormalExport>()
        .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);

    // Get asset name from filename if no blueprint export
    var bpName = bpExport?.ObjectName.ToString() ?? classExport?.ObjectName.ToString() ?? Path.GetFileNameWithoutExtension(args[1]);

    // Parent class - try multiple strategies
    var parentClass = "Unknown";

    // Strategy 1: ClassExport.SuperStruct (works when widget has BP logic)
    if (classExport?.SuperStruct != null && classExport.SuperStruct.Index != 0)
    {
        parentClass = ResolvePackageIndex(asset, classExport.SuperStruct);
        if (parentClass.Contains("_C"))
            parentClass = parentClass.Replace("_C", "");
    }

    // Strategy 2: Check ParentClass property on ANY NormalExport that might be the blueprint
    if (parentClass == "Unknown" || parentClass == "[null]")
    {
        foreach (var export in asset.Exports.OfType<NormalExport>())
        {
            if (export.Data == null) continue;
            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "ParentClass" || propName == "NativeParentClass")
                {
                    if (prop is ObjectPropertyData objProp && objProp.Value.Index != 0)
                    {
                        var resolved = ResolvePackageIndex(asset, objProp.Value);
                        if (!string.IsNullOrEmpty(resolved) && resolved != "[null]")
                        {
                            parentClass = resolved.Replace("_C", "");
                            break;
                        }
                    }
                }
            }
            if (parentClass != "Unknown" && parentClass != "[null]") break;
        }
    }

    // Strategy 3: Look for parent class in imports - be more inclusive
    // For widgets, the parent is typically a BlueprintGeneratedClass import
    if (parentClass == "Unknown" || parentClass == "[null]")
    {
        var bpClassName = bpName + "_C";
        string bestCandidate = null;

        foreach (var import in asset.Imports)
        {
            var importName = import.ObjectName.ToString();
            var importClass = import.ClassName?.ToString() ?? "";

            // Skip this widget's own class
            if (importName == bpClassName) continue;

            // Look for BlueprintGeneratedClass imports (parent widget classes)
            if (importClass == "BlueprintGeneratedClass" && importName.EndsWith("_C"))
            {
                var baseName = importName[..^2];
                // Prefer HUD/Layout classes as they're more likely to be parents
                if (baseName.Contains("HUD") || baseName.Contains("Layout") ||
                    baseName.Contains("Activatable"))
                {
                    parentClass = baseName;
                    break;
                }
                // Otherwise keep as candidate
                if (bestCandidate == null)
                    bestCandidate = baseName;
            }
            // Also check Class imports
            else if (importClass == "Class" && importName.EndsWith("_C"))
            {
                var baseName = importName[..^2];
                if (baseName.Contains("Widget") || baseName.Contains("UserWidget") ||
                    baseName.Contains("HUD") || baseName.Contains("Layout") ||
                    baseName.Contains("Activatable"))
                {
                    if (bestCandidate == null)
                        bestCandidate = baseName;
                }
            }
        }

        if ((parentClass == "Unknown" || parentClass == "[null]") && bestCandidate != null)
            parentClass = bestCandidate;
    }

    // Strategy 4: For pure layout widgets, find parent by excluding engine widget classes
    // Look for BlueprintGeneratedClass imports whose outer is NOT a core engine package
    if (parentClass == "Unknown" || parentClass == "[null]")
    {
        var bpClassName = bpName + "_C";

        foreach (var import in asset.Imports)
        {
            var importName = import.ObjectName.ToString();
            var importClass = import.ClassName?.ToString() ?? "";

            if (importName == bpClassName) continue;

            if (importClass == "BlueprintGeneratedClass" && importName.EndsWith("_C"))
            {
                // Check if outer is project/plugin code, not engine
                var outerIdx = import.OuterIndex.Index;
                if (outerIdx < 0)  // Negative index = import reference
                {
                    var outer = asset.Imports[-outerIdx - 1];
                    var outerName = outer.ObjectName.ToString();
                    // Skip core engine packages (UMG, Slate, CommonUI engine classes)
                    if (outerName.StartsWith("/Script/UMG") ||
                        outerName.StartsWith("/Script/Slate") ||
                        outerName == "/Script/Engine" ||
                        outerName == "/Script/CoreUObject")
                        continue;

                    // This is likely the parent widget class from project/plugin
                    parentClass = importName[..^2];
                    break;
                }
            }
        }
    }

    // Interfaces
    var interfaces = new List<string>();
    if (classExport?.Interfaces != null)
    {
        foreach (var iface in classExport.Interfaces)
        {
            try
            {
                string ifaceName;
                var classField = iface.GetType().GetField("Class", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (classField != null)
                {
                    var classValue = classField.GetValue(iface);
                    if (classValue is FPackageIndex pkgIndex)
                        ifaceName = ResolvePackageIndex(asset, pkgIndex);
                    else if (classValue is int intIndex)
                        ifaceName = ResolvePackageIndex(asset, new FPackageIndex(intIndex));
                    else
                        continue;

                    if (!string.IsNullOrEmpty(ifaceName) && ifaceName != "[null]")
                    {
                        var cleanName = ifaceName.EndsWith("_C") ? ifaceName[..^2] : ifaceName;
                        interfaces.Add(cleanName);
                    }
                }
            }
            catch { }
        }
    }

    // Events and Functions
    var events = new List<string>();
    var functions = new List<(string name, string flags)>();
    foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
    {
        var funcName = funcExport.ObjectName.ToString();
        var flags = funcExport.FunctionFlags.ToString();
        if (funcName.StartsWith("ExecuteUbergraph") || funcName.StartsWith("bpv__") ||
            funcName.StartsWith("__") || funcName.StartsWith("InpActEvt_") ||
            funcName.StartsWith("InpAxisEvt_") || funcName.StartsWith("K2Node_") ||
            funcName.Contains("__TRASHFUNC")) continue;

        bool isEvent = funcName.StartsWith("Receive") || funcName.StartsWith("OnRep_") ||
                      (flags.Contains("BlueprintEvent") && !flags.Contains("BlueprintCallable"));

        if (isEvent)
            events.Add(funcName);
        else
        {
            var simpleFlags = new List<string>();
            if (flags.Contains("BlueprintCallable")) simpleFlags.Add("Callable");
            if (flags.Contains("BlueprintPure")) simpleFlags.Add("Pure");
            if (flags.Contains("BlueprintEvent")) simpleFlags.Add("Event");
            functions.Add((funcName, string.Join(",", simpleFlags)));
        }
    }

    // Variables
    var variables = new List<(string name, string type)>();
    foreach (var export in asset.Exports)
    {
        var className = export.GetExportClassType()?.ToString() ?? "";
        if (className.EndsWith("Property"))
        {
            var propName = export.ObjectName.ToString();
            if (propName.StartsWith("bpv__") || propName.StartsWith("K2Node_") ||
                propName.StartsWith("Uber") || propName == "None") continue;

            var outer = export.OuterIndex.Index;
            if (outer > 0 && outer <= asset.Exports.Count)
            {
                var outerClass = asset.Exports[outer - 1].GetExportClassType()?.ToString() ?? "";
                if (outerClass.Contains("Function")) continue;
            }

            var propType = className.Replace("Property", "");
            if (!variables.Any(v => v.name == propName))
                variables.Add((propName, propType));
        }
    }

    // Write blueprint metadata
    if (parentClass != "Unknown" && parentClass != "[null]")
        xml.AppendLine($"  <parent-class>{EscapeXml(parentClass)}</parent-class>");

    if (interfaces.Count > 0)
    {
        xml.AppendLine("  <interfaces>");
        foreach (var iface in interfaces)
            xml.AppendLine($"    <interface>{EscapeXml(iface)}</interface>");
        xml.AppendLine("  </interfaces>");
    }

    if (events.Count > 0)
    {
        xml.AppendLine("  <events>");
        foreach (var evt in events.Take(15))
            xml.AppendLine($"    <event>{EscapeXml(evt)}</event>");
        xml.AppendLine("  </events>");
    }

    if (functions.Count > 0)
    {
        xml.AppendLine("  <functions>");
        foreach (var (name, flags) in functions.Take(20))
        {
            if (!string.IsNullOrEmpty(flags))
                xml.AppendLine($"    <function flags=\"{EscapeXml(flags)}\">{EscapeXml(name)}</function>");
            else
                xml.AppendLine($"    <function>{EscapeXml(name)}</function>");
        }
        xml.AppendLine("  </functions>");
    }

    if (variables.Count > 0)
    {
        xml.AppendLine("  <variables>");
        foreach (var (name, type) in variables.Take(30))
            xml.AppendLine($"    <variable type=\"{EscapeXml(type)}\">{EscapeXml(name)}</variable>");
        xml.AppendLine("  </variables>");
    }

    // Find the WidgetTree export - this contains the actual widget structure
    int widgetTreeIndex = -1;
    for (int i = 0; i < asset.Exports.Count; i++)
    {
        if (asset.Exports[i].ObjectName.ToString() == "WidgetTree")
        {
            widgetTreeIndex = i + 1;
            break;
        }
    }

    // Build slot-to-content mapping (slots point to their content widget)
    var slotToContent = new Dictionary<int, int>();
    var parentFromSlot = new Dictionary<int, int>();

    for (int i = 0; i < asset.Exports.Count; i++)
    {
        var export = asset.Exports[i];
        var className = export.GetExportClassType()?.ToString() ?? "";

        if (className.Contains("Slot") && export is NormalExport slotExport && slotExport.Data != null)
        {
            int slotIndex = i + 1;
            foreach (var prop in slotExport.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "Content" && prop is ObjectPropertyData contentProp)
                {
                    if (contentProp.Value.Index > 0)
                        slotToContent[slotIndex] = contentProp.Value.Index;
                }
                else if (propName == "Parent" && prop is ObjectPropertyData parentProp)
                {
                    if (parentProp.Value.Index > 0)
                        parentFromSlot[slotIndex] = parentProp.Value.Index;
                }
            }
        }
    }

    // Build widget info, tracking parent through slot system
    var widgetsByIndex = new Dictionary<int, (string name, string type, int parentIndex, Dictionary<string, object> props)>();
    var childrenByParent = new Dictionary<int, List<int>>();

    for (int i = 0; i < asset.Exports.Count; i++)
    {
        var export = asset.Exports[i];
        var className = export.GetExportClassType()?.ToString() ?? "";
        var exportName = export.ObjectName.ToString();
        var exportIndex = i + 1;

        // Skip non-widget exports
        if (!IsWidgetClass(className)) continue;

        // Skip slots and WidgetTree
        if (className.Contains("Slot")) continue;
        if (exportName == "WidgetTree" || className == "WidgetTree") continue;

        // Skip generated class and blueprint asset exports
        if (className.Contains("GeneratedClass") || className == "WidgetBlueprint") continue;

        // Only include widgets that are under the WidgetTree
        bool isUnderWidgetTree = false;
        var currentOuter = export.OuterIndex.Index;
        while (currentOuter > 0 && currentOuter <= asset.Exports.Count)
        {
            if (currentOuter == widgetTreeIndex)
            {
                isUnderWidgetTree = true;
                break;
            }
            currentOuter = asset.Exports[currentOuter - 1].OuterIndex.Index;
        }
        if (!isUnderWidgetTree) continue;

        // Extract important properties
        var props = new Dictionary<string, object>();
        int parentViaSlot = 0;

        if (export is NormalExport normalExport && normalExport.Data != null)
        {
            foreach (var prop in normalExport.Data)
            {
                var propName = prop.Name.ToString();

                // Track parent via Slot property
                if (propName == "Slot" && prop is ObjectPropertyData slotProp)
                {
                    var slotIdx = slotProp.Value.Index;
                    if (slotIdx > 0 && parentFromSlot.TryGetValue(slotIdx, out var pIdx))
                        parentViaSlot = pIdx;
                }
                // Extract text content
                else if (propName == "Text")
                {
                    if (prop is TextPropertyData textProp)
                    {
                        var textVal = GetTextPropertyValue(textProp);
                        if (!string.IsNullOrEmpty(textVal))
                            props["text"] = textVal;
                    }
                }
                // Extract visibility
                else if (propName == "Visibility")
                {
                    var val = GetPropertyValue(prop, 0);
                    if (val != null && val.ToString() != "Visible" && val.ToString() != "0")
                        props["visibility"] = val;
                }
            }
        }

        widgetsByIndex[exportIndex] = (exportName, className, parentViaSlot, props);

        if (!childrenByParent.ContainsKey(parentViaSlot))
            childrenByParent[parentViaSlot] = new List<int>();
        childrenByParent[parentViaSlot].Add(exportIndex);
    }

    // Find root widgets (parentIndex == 0)
    var rootWidgets = childrenByParent.GetValueOrDefault(0, new List<int>());

    xml.AppendLine($"  <summary widget-count=\"{widgetsByIndex.Count}\" />");
    xml.AppendLine("  <hierarchy>");

    // Write tree recursively
    foreach (var rootIdx in rootWidgets)
    {
        WriteWidgetXml(xml, rootIdx, widgetsByIndex, childrenByParent, 2);
    }

    xml.AppendLine("  </hierarchy>");
    xml.AppendLine("</widget-blueprint>");

    Console.WriteLine(xml.ToString());
}

bool IsWidgetClass(string className)
{
    return className.Contains("Widget") ||
           className.Contains("Panel") ||
           className.Contains("Overlay") ||
           className.Contains("Border") ||
           className.Contains("Button") ||
           className.Contains("Text") ||
           className.Contains("Image") ||
           className.Contains("Slot") ||
           className.Contains("Canvas") ||
           className.Contains("Box") ||
           className.Contains("Grid") ||
           className.Contains("Spacer") ||
           className.Contains("ScrollBox") ||
           className.Contains("ListView") ||
           className.Contains("TileView") ||
           className.Contains("Slider") ||
           className.Contains("ProgressBar") ||
           className.Contains("CheckBox") ||
           className.Contains("ComboBox") ||
           className.Contains("EditableText") ||
           className.Contains("RichText") ||
           className.Contains("Throbber") ||
           className.Contains("Separator") ||
           className.Contains("Wrap") ||
           className.Contains("Switcher") ||
           className.Contains("Scale") ||
           className.Contains("Safe") ||
           className.Contains("RetainerBox") ||
           className.Contains("Named");
}

void WriteWidgetXml(System.Text.StringBuilder xml, int widgetIndex,
    Dictionary<int, (string name, string type, int parentIndex, Dictionary<string, object> props)> widgets,
    Dictionary<int, List<int>> children, int depth)
{
    if (!widgets.TryGetValue(widgetIndex, out var widget)) return;

    var indent = new string(' ', depth * 2);
    var hasChildren = children.ContainsKey(widgetIndex) && children[widgetIndex].Count > 0;
    var hasProps = widget.props.Count > 0;

    // Simplify type name
    var simpleType = widget.type.Replace("CommonUI", "").Replace("User", "");

    // Build attributes string
    var attrs = $"name=\"{EscapeXml(widget.name)}\" type=\"{EscapeXml(simpleType)}\"";

    // Add text inline if present
    if (widget.props.TryGetValue("text", out var text))
    {
        attrs += $" text=\"{EscapeXml(text?.ToString() ?? "")}\"";
    }

    if (!hasChildren && widget.props.Count <= 1)
    {
        // Self-closing for leaf widgets with no special props
        xml.AppendLine($"{indent}<widget {attrs} />");
    }
    else
    {
        xml.AppendLine($"{indent}<widget {attrs}>");

        // Write properties (except text which is inline)
        foreach (var prop in widget.props.Where(p => p.Key != "text"))
        {
            xml.AppendLine($"{indent}  <{prop.Key}>{EscapeXml(prop.Value?.ToString() ?? "")}</{prop.Key}>");
        }

        // Write children
        if (hasChildren)
        {
            foreach (var childIdx in children[widgetIndex])
            {
                WriteWidgetXml(xml, childIdx, widgets, children, depth + 1);
            }
        }

        xml.AppendLine($"{indent}</widget>");
    }
}

// ============================================================================
// DATATABLE - Extract DataTable rows as XML
// ============================================================================
void ExtractDataTable(UAsset asset)
{
    var xml = new System.Text.StringBuilder();

    var dtExport = asset.Exports.OfType<DataTableExport>().FirstOrDefault();
    if (dtExport?.Table?.Data == null)
    {
        xml.AppendLine("<datatable>");
        xml.AppendLine("  <error>No DataTable found in asset</error>");
        xml.AppendLine("</datatable>");
        Console.WriteLine(xml.ToString());
        return;
    }

    var rowStruct = dtExport.Table.Data.FirstOrDefault()?.StructType?.ToString() ?? "Unknown";
    var rowCount = dtExport.Table.Data.Count;

    xml.AppendLine("<datatable>");
    xml.AppendLine($"  <row-struct>{EscapeXml(rowStruct)}</row-struct>");
    xml.AppendLine($"  <row-count>{rowCount}</row-count>");

    // Extract column names from first row
    if (dtExport.Table.Data.Count > 0 && dtExport.Table.Data[0].Value != null)
    {
        xml.AppendLine("  <columns>");
        foreach (var prop in dtExport.Table.Data[0].Value)
        {
            var colName = prop.Name.ToString();
            var colType = prop.PropertyType?.ToString() ?? "Unknown";
            xml.AppendLine($"    <column name=\"{EscapeXml(colName)}\" type=\"{EscapeXml(colType)}\" />");
        }
        xml.AppendLine("  </columns>");
    }

    xml.AppendLine("  <rows>");

    // Limit rows to avoid huge output
    const int maxRows = 25;
    var rowsToShow = dtExport.Table.Data.Take(maxRows);

    foreach (var row in rowsToShow)
    {
        var rowName = row.Name.ToString();
        xml.Append($"    <row key=\"{EscapeXml(rowName)}\"");

        if (row.Value != null)
        {
            // For simple rows, inline as attributes
            if (row.Value.Count <= 6 && row.Value.All(p => IsSimpleProperty(p)))
            {
                foreach (var prop in row.Value)
                {
                    var propName = prop.Name.ToString();
                    var propVal = GetPropertyValue(prop, 0);
                    xml.Append($" {EscapeXml(propName)}=\"{EscapeXml(propVal?.ToString() ?? "")}\"");
                }
                xml.AppendLine(" />");
            }
            else
            {
                // Complex row - use nested elements
                xml.AppendLine(">");
                foreach (var prop in row.Value)
                {
                    var propName = prop.Name.ToString();
                    var propVal = GetPropertyValue(prop, 1);
                    if (propVal is Dictionary<string, object> dict)
                    {
                        xml.AppendLine($"      <{EscapeXml(propName)}>{FormatDictAsAttributes(dict)}</{EscapeXml(propName)}>");
                    }
                    else
                    {
                        xml.AppendLine($"      <{EscapeXml(propName)}>{EscapeXml(propVal?.ToString() ?? "")}</{EscapeXml(propName)}>");
                    }
                }
                xml.AppendLine("    </row>");
            }
        }
        else
        {
            xml.AppendLine(" />");
        }
    }

    if (rowCount > maxRows)
        xml.AppendLine($"    <!-- and {rowCount - maxRows} more rows -->");

    xml.AppendLine("  </rows>");
    xml.AppendLine("</datatable>");

    Console.WriteLine(xml.ToString());
}

bool IsSimpleProperty(PropertyData prop)
{
    return prop is IntPropertyData || prop is FloatPropertyData || prop is DoublePropertyData ||
           prop is BoolPropertyData || prop is StrPropertyData || prop is NamePropertyData ||
           prop is BytePropertyData || prop is EnumPropertyData;
}

string FormatDictAsAttributes(Dictionary<string, object> dict)
{
    return string.Join(" ", dict.Select(kv => $"{kv.Key}=\"{EscapeXml(FormatValue(kv.Value))}\""));
}

string FormatValue(object value)
{
    if (value == null) return "";

    if (value is Dictionary<string, object> nestedDict)
    {
        // Format nested struct as key=value pairs
        var parts = nestedDict.Select(kv => $"{kv.Key}={FormatValue(kv.Value)}");
        return "{" + string.Join(", ", parts) + "}";
    }

    if (value is List<object> list)
    {
        // Format list as comma-separated values
        var items = list.Select(item => FormatValue(item));
        return "[" + string.Join(", ", items) + "]";
    }

    return value.ToString() ?? "";
}

// ============================================================================
// BLUEPRINT - Extract Blueprint as focused XML
// ============================================================================
void ExtractBlueprint(UAsset asset)
{
    var xml = new System.Text.StringBuilder();
    xml.AppendLine("<blueprint>");

    // Find the main blueprint class export for parent info
    var classExport = asset.Exports.OfType<ClassExport>().FirstOrDefault();
    var bpExport = asset.Exports
        .OfType<NormalExport>()
        .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);

    // Name
    var bpName = bpExport?.ObjectName.ToString() ?? classExport?.ObjectName.ToString() ?? "Unknown";
    xml.AppendLine($"  <name>{EscapeXml(bpName)}</name>");

    // Parent class - resolve from SuperStruct FPackageIndex
    var parentClass = "Unknown";
    if (classExport?.SuperStruct != null && classExport.SuperStruct.Index != 0)
    {
        parentClass = ResolvePackageIndex(asset, classExport.SuperStruct);
        // Clean up the parent class name
        if (parentClass.Contains("_C"))
            parentClass = parentClass.Replace("_C", "");
    }

    // Fallback: Look for parent class in imports if SuperStruct didn't work
    if (parentClass == "Unknown" || parentClass == "[null]")
    {
        // The parent class is typically imported - look for Class imports that could be the parent
        // Common patterns: imports with "_C" suffix that aren't the blueprint itself
        var bpClassName = bpName + "_C";
        foreach (var import in asset.Imports)
        {
            var importName = import.ObjectName.ToString();
            var importClass = import.ClassName?.ToString() ?? "";

            // Skip the blueprint's own class
            if (importName == bpClassName) continue;

            // Look for Class imports that end in _C (compiled blueprint classes)
            if (importClass == "Class" && importName.EndsWith("_C"))
            {
                parentClass = importName.Replace("_C", "");
                break;
            }
            // Look for BlueprintGeneratedClass imports
            if (importClass == "BlueprintGeneratedClass" && importName.EndsWith("_C"))
            {
                parentClass = importName.Replace("_C", "");
                break;
            }
        }
    }

    // Fallback 2: Check for parent in NativeClass property of blueprint export
    if ((parentClass == "Unknown" || parentClass == "[null]") && bpExport?.Data != null)
    {
        foreach (var prop in bpExport.Data)
        {
            if (prop.Name.ToString() == "ParentClass" || prop.Name.ToString() == "NativeParentClass")
            {
                if (prop is ObjectPropertyData objProp && objProp.Value.Index != 0)
                {
                    var resolved = ResolvePackageIndex(asset, objProp.Value);
                    if (!string.IsNullOrEmpty(resolved) && resolved != "[null]")
                    {
                        parentClass = resolved.Replace("_C", "");
                        break;
                    }
                }
            }
        }
    }
    xml.AppendLine($"  <parent>{EscapeXml(parentClass)}</parent>");

    // Interfaces
    var interfaces = new List<string>();
    if (classExport?.Interfaces != null)
    {
        foreach (var iface in classExport.Interfaces)
        {
            // iface.Class is a FPackageIndex in older UAssetAPI, int in newer
            try
            {
                string ifaceName;
                var classField = iface.GetType().GetField("Class", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (classField != null)
                {
                    var classValue = classField.GetValue(iface);
                    if (classValue is FPackageIndex pkgIndex)
                        ifaceName = ResolvePackageIndex(asset, pkgIndex);
                    else if (classValue is int intIndex)
                        ifaceName = ResolvePackageIndex(asset, new FPackageIndex(intIndex));
                    else
                        continue;

                    if (!string.IsNullOrEmpty(ifaceName) && ifaceName != "[null]")
                        interfaces.Add(ifaceName);
                }
            }
            catch { /* skip this interface */ }
        }
    }
    if (interfaces.Count > 0)
    {
        xml.AppendLine("  <interfaces>");
        foreach (var iface in interfaces)
        {
            // Clean up interface name (remove _C suffix)
            var cleanName = iface.EndsWith("_C") ? iface[..^2] : iface;
            xml.AppendLine($"    <interface>{EscapeXml(cleanName)}</interface>");
        }
        xml.AppendLine("  </interfaces>");
    }

    // Components - find exports that look like components
    var components = new List<(string name, string type)>();
    foreach (var export in asset.Exports)
    {
        var className = export.GetExportClassType()?.ToString() ?? "";
        var exportName = export.ObjectName.ToString();

        // Skip internal/generated stuff
        if (exportName.StartsWith("Default__")) continue;
        if (className.Contains("Function")) continue;
        if (className.Contains("BlueprintGeneratedClass")) continue;
        if (className.Contains("Blueprint") && !className.Contains("Component")) continue;

        // Check if it's a component
        if (className.Contains("Component") ||
            className.Contains("SceneComponent") ||
            className.Contains("ActorComponent"))
        {
            // Clean up type name
            var typeName = className;
            if (typeName.Contains("_C"))
                typeName = typeName.Replace("_C", "");
            components.Add((exportName, typeName));
        }
    }
    if (components.Count > 0)
    {
        xml.AppendLine("  <components>");
        foreach (var (name, type) in components.Take(20)) // Limit to 20
        {
            // Clean up component name (remove _GEN_VARIABLE suffix)
            var cleanName = name.Replace("_GEN_VARIABLE", "");
            xml.AppendLine($"    <component type=\"{EscapeXml(type)}\">{EscapeXml(cleanName)}</component>");
        }
        if (components.Count > 20)
            xml.AppendLine($"    <!-- and {components.Count - 20} more -->");
        xml.AppendLine("  </components>");
    }

    // Events and Functions - separate them
    var events = new List<string>();
    var functions = new List<(string name, string flags, List<string> calls, List<(string name, string type, string direction)> parameters)>();

    foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
    {
        var funcName = funcExport.ObjectName.ToString();
        var flags = funcExport.FunctionFlags.ToString();

        // Skip internal/auto-generated functions
        if (funcName.StartsWith("ExecuteUbergraph")) continue;
        if (funcName.StartsWith("bpv__")) continue;
        if (funcName.StartsWith("__")) continue;
        if (funcName.StartsWith("InpActEvt_")) continue;  // Auto-generated input events
        if (funcName.StartsWith("InpAxisEvt_")) continue;
        if (funcName.StartsWith("InpAxisKeyEvt_")) continue;
        if (funcName.StartsWith("InpTchEvt_")) continue;
        if (funcName.StartsWith("K2Node_")) continue;
        if (funcName.Contains("__TRASHFUNC")) continue;
        if (funcName.Contains("__TRASHEVENT")) continue;

        // Check if it's an event (UE standard events)
        bool isEvent = funcName.StartsWith("Receive") ||
                      funcName == "ReceiveBeginPlay" ||
                      funcName == "ReceiveTick" ||
                      funcName == "ReceiveEndPlay" ||
                      funcName == "ReceiveHit" ||
                      funcName == "ReceiveAnyDamage" ||
                      funcName == "ReceiveActorBeginOverlap" ||
                      funcName == "ReceiveActorEndOverlap" ||
                      funcName.StartsWith("OnRep_") ||
                      (flags.Contains("BlueprintEvent") && !flags.Contains("BlueprintCallable"));

        // Simplify flags for display
        var simpleFlags = new List<string>();
        if (flags.Contains("BlueprintCallable")) simpleFlags.Add("Callable");
        if (flags.Contains("BlueprintPure")) simpleFlags.Add("Pure");
        if (flags.Contains("BlueprintEvent")) simpleFlags.Add("Event");
        if (flags.Contains("Native")) simpleFlags.Add("Native");

        // Extract function calls via bytecode analysis
        var funcCalls = new List<string>();
        if (funcExport.ScriptBytecode != null && funcExport.ScriptBytecode.Length > 0)
        {
            var calls = new HashSet<string>();
            var vars = new HashSet<string>();
            var casts = new HashSet<string>();
            foreach (var expr in funcExport.ScriptBytecode)
            {
                AnalyzeExpression(asset, expr, calls, vars, casts);
            }
            // Filter out noise and internal calls
            funcCalls = calls
                .Where(c => !string.IsNullOrEmpty(c) && c != "[null]" && !c.StartsWith("["))
                .Where(c => !c.StartsWith("K2Node_") && !c.Contains("__"))
                .OrderBy(c => c)
                .Take(10)  // Limit to top 10 calls
                .ToList();
        }

        // Extract function parameters from LoadedProperties
        var parameters = new List<(string name, string type, string direction)>();
        if (funcExport.LoadedProperties != null && funcExport.LoadedProperties.Length > 0)
        {
            foreach (var prop in funcExport.LoadedProperties)
            {
                if (!prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_Parm)) continue;

                var paramName = prop.Name?.ToString() ?? "Unknown";
                var paramType = prop.SerializedType?.ToString() ?? "Unknown";
                paramType = paramType.Replace("Property", "");

                string direction;
                if (prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReturnParm))
                    direction = "return";
                else if (prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_OutParm)
                         && !prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReferenceParm))
                    direction = "out";
                else
                    direction = "in";

                parameters.Add((paramName, paramType, direction));
            }
        }
        else
        {
            // Fallback: find property exports whose OuterIndex points to this function
            var funcIndex = Array.IndexOf(asset.Exports.ToArray(), funcExport) + 1;
            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!className.EndsWith("Property")) continue;
                if (export.OuterIndex.Index != funcIndex) continue;

                var paramName = export.ObjectName.ToString();
                var paramType = className.Replace("Property", "");
                parameters.Add((paramName, paramType, "in"));
            }
        }

        if (isEvent)
        {
            events.Add(funcName);
        }
        else
        {
            functions.Add((funcName, string.Join(",", simpleFlags), funcCalls, parameters));
        }
    }

    if (events.Count > 0)
    {
        xml.AppendLine("  <events>");
        foreach (var evt in events.Take(15))
            xml.AppendLine($"    <event>{EscapeXml(evt)}</event>");
        if (events.Count > 15)
            xml.AppendLine($"    <!-- and {events.Count - 15} more -->");
        xml.AppendLine("  </events>");
    }

    if (functions.Count > 0)
    {
        xml.AppendLine("  <functions>");
        foreach (var (name, flags, calls, parameters) in functions.Take(25))
        {
            bool hasContent = calls.Count > 0 || parameters.Count > 0;
            if (hasContent)
            {
                var flagsAttr = !string.IsNullOrEmpty(flags) ? $" flags=\"{EscapeXml(flags)}\"" : "";
                xml.AppendLine($"    <function name=\"{EscapeXml(name)}\"{flagsAttr}>");

                if (parameters.Count > 0)
                {
                    xml.AppendLine("      <params>");
                    foreach (var (pName, pType, pDir) in parameters)
                        xml.AppendLine($"        <param name=\"{EscapeXml(pName)}\" type=\"{EscapeXml(pType)}\" direction=\"{pDir}\"/>");
                    xml.AppendLine("      </params>");
                }

                if (calls.Count > 0)
                    xml.AppendLine($"      <calls>{EscapeXml(string.Join(", ", calls))}</calls>");

                xml.AppendLine("    </function>");
            }
            else
            {
                // Single-line format for simple functions
                if (!string.IsNullOrEmpty(flags))
                    xml.AppendLine($"    <function flags=\"{EscapeXml(flags)}\">{EscapeXml(name)}</function>");
                else
                    xml.AppendLine($"    <function>{EscapeXml(name)}</function>");
            }
        }
        if (functions.Count > 25)
            xml.AppendLine($"    <!-- and {functions.Count - 25} more -->");
        xml.AppendLine("  </functions>");
    }

    // Variables - look for property exports that represent BP variables
    var variables = new List<(string name, string type)>();

    // Find property exports - these are the actual BP-defined variables
    foreach (var export in asset.Exports)
    {
        var className = export.GetExportClassType()?.ToString() ?? "";

        // Property exports represent variables
        if (className.EndsWith("Property"))
        {
            var propName = export.ObjectName.ToString();

            // Skip internal/auto-generated properties
            if (propName.StartsWith("bpv__")) continue;
            if (propName.StartsWith("K2Node_")) continue;
            if (propName.StartsWith("Uber")) continue;
            if (propName == "None") continue;

            // Check if it belongs to the generated class (not a function parameter)
            var outer = export.OuterIndex.Index;
            if (outer > 0 && outer <= asset.Exports.Count)
            {
                var outerExport = asset.Exports[outer - 1];
                var outerClass = outerExport.GetExportClassType()?.ToString() ?? "";

                // Only include if the outer is the generated class, not a function
                if (outerClass.Contains("Function")) continue;
            }

            // Extract type from class name (e.g., "FloatProperty" -> "Float")
            var propType = className
                .Replace("Property", "")
                .Replace("FObject", "Object")
                .Replace("FStruct", "Struct")
                .Replace("FArray", "Array")
                .Replace("FBool", "Bool")
                .Replace("FFloat", "Float")
                .Replace("FInt", "Int")
                .Replace("FStr", "String")
                .Replace("FName", "Name")
                .Replace("FByte", "Byte")
                .Replace("FClass", "Class")
                .Replace("FSoftObject", "SoftObject")
                .Replace("FText", "Text");

            if (!variables.Any(v => v.name == propName))
                variables.Add((propName, propType));
        }
    }

    if (variables.Count > 0)
    {
        xml.AppendLine("  <variables>");
        foreach (var (name, type) in variables.Take(30))
            xml.AppendLine($"    <variable type=\"{EscapeXml(type)}\">{EscapeXml(name)}</variable>");
        if (variables.Count > 30)
            xml.AppendLine($"    <!-- and {variables.Count - 30} more -->");
        xml.AppendLine("  </variables>");
    }

    xml.AppendLine("</blueprint>");
    Console.WriteLine(xml.ToString());
}

string EscapeXml(string text)
{
    if (string.IsNullOrEmpty(text)) return "";
    return text
        .Replace("&", "&amp;")
        .Replace("<", "&lt;")
        .Replace(">", "&gt;")
        .Replace("\"", "&quot;")
        .Replace("'", "&apos;");
}

// ============================================================================
// MATERIAL - Extract Material/MaterialInstance parameters as XML
// ============================================================================
void ExtractMaterial(UAsset asset)
{
    var xml = new System.Text.StringBuilder();

    // Find material exports - must be Material or MaterialInstance, not MaterialExpression
    // Check all export types (NormalExport, RawExport, etc.)
    var materialExportBase = asset.Exports
        .FirstOrDefault(e =>
        {
            var cn = e.GetExportClassType()?.ToString() ?? "";
            // Match Material, MaterialInstance, MaterialInstanceConstant, etc.
            // But NOT MaterialExpression*, MaterialFunction*
            return (cn == "Material" ||
                    cn.StartsWith("MaterialInstance") ||
                    cn == "MaterialFunction") &&
                   !cn.Contains("Expression");
        });

    if (materialExportBase == null)
    {
        xml.AppendLine("<material>");
        xml.AppendLine("  <error>No Material found in asset</error>");
        xml.AppendLine("</material>");
        Console.WriteLine(xml.ToString());
        return;
    }

    var className = materialExportBase.GetExportClassType()?.ToString() ?? "";
    var isInstance = className.Contains("Instance");
    var matName = materialExportBase.ObjectName.ToString();

    // Try to cast to NormalExport for property access (MaterialInstances)
    var materialExport = materialExportBase as NormalExport;

    // Collect parameters
    var scalarParams = new List<(string name, object value, string group)>();
    var vectorParams = new List<(string name, List<object> value, string group)>();
    var textureParams = new List<(string name, string texture, string group)>();
    var staticSwitches = new List<(string name, bool value)>();

    string domain = "Surface";
    string blendMode = "Opaque";
    string shadingModel = "DefaultLit";
    string parent = "";

    if (materialExport?.Data != null)
    {
        foreach (var prop in materialExport.Data)
        {
            var propName = prop.Name.ToString();

            if (propName == "MaterialDomain")
                domain = GetPropertyValue(prop, 0)?.ToString() ?? "Surface";
            else if (propName == "BlendMode")
                blendMode = GetPropertyValue(prop, 0)?.ToString() ?? "Opaque";
            else if (propName == "ShadingModel" || propName == "ShadingModels")
                shadingModel = GetPropertyValue(prop, 0)?.ToString() ?? "DefaultLit";
            else if (propName == "Parent" && prop is ObjectPropertyData parentProp)
                parent = ResolvePackageIndex(asset, parentProp.Value);
            else if (propName == "Parent")
                parent = GetPropertyValue(prop, 0)?.ToString() ?? "";
            else if (propName == "ScalarParameterValues" && prop is ArrayPropertyData scalarArray)
                ExtractScalarParametersXml(scalarArray, scalarParams);
            else if (propName == "VectorParameterValues" && prop is ArrayPropertyData vectorArray)
                ExtractVectorParametersXml(vectorArray, vectorParams);
            else if (propName == "TextureParameterValues" && prop is ArrayPropertyData textureArray)
                ExtractTextureParametersXml(asset, textureArray, textureParams);
            else if ((propName == "StaticParametersRuntime" || propName == "StaticParameters") && prop is StructPropertyData staticStruct)
                ExtractStaticSwitchesXml(staticStruct, staticSwitches);
        }
    }

    // Output XML
    xml.AppendLine(isInstance ? "<material-instance>" : "<material>");

    xml.AppendLine($"  <name>{EscapeXml(matName)}</name>");

    if (isInstance && !string.IsNullOrEmpty(parent) && parent != "[null]")
        xml.AppendLine($"  <parent>{EscapeXml(parent)}</parent>");

    xml.AppendLine($"  <domain>{EscapeXml(domain)}</domain>");
    xml.AppendLine($"  <blend-mode>{EscapeXml(blendMode)}</blend-mode>");
    xml.AppendLine($"  <shading-model>{EscapeXml(shadingModel)}</shading-model>");

    // Parameters section
    if (scalarParams.Count > 0 || vectorParams.Count > 0 || textureParams.Count > 0)
    {
        xml.AppendLine("  <parameters>");

        foreach (var (name, value, group) in scalarParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            xml.AppendLine($"    <scalar name=\"{EscapeXml(name)}\" value=\"{value}\"{groupAttr} />");
        }

        foreach (var (name, value, group) in vectorParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            var rgba = string.Join(",", value.Select(v => v?.ToString() ?? "0"));
            xml.AppendLine($"    <vector name=\"{EscapeXml(name)}\" rgba=\"{rgba}\"{groupAttr} />");
        }

        foreach (var (name, texture, group) in textureParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            xml.AppendLine($"    <texture name=\"{EscapeXml(name)}\" ref=\"{EscapeXml(texture)}\"{groupAttr} />");
        }

        xml.AppendLine("  </parameters>");
    }

    // Static switches for instances
    if (staticSwitches.Count > 0)
    {
        xml.AppendLine("  <static-switches>");
        foreach (var (name, value) in staticSwitches)
        {
            xml.AppendLine($"    <switch name=\"{EscapeXml(name)}\" value=\"{value.ToString().ToLower()}\" />");
        }
        xml.AppendLine("  </static-switches>");
    }

    // For base materials (not instances), extract expression nodes
    if (!isInstance)
    {
        var expressionNodes = new List<(string type, string name, string details)>();
        var texturesUsed = new HashSet<string>();

        foreach (var export in asset.Exports)
        {
            var expClassName = export.GetExportClassType()?.ToString() ?? "";
            if (!expClassName.StartsWith("MaterialExpression")) continue;

            var expName = export.ObjectName.ToString();
            var nodeType = expClassName.Replace("MaterialExpression", "");

            // Extract parameter name and value for parameter nodes
            if (export is NormalExport normalExp && normalExp.Data != null)
            {
                string paramName = "";
                string paramValue = "";
                string paramGroup = "";

                foreach (var prop in normalExp.Data)
                {
                    var pn = prop.Name.ToString();
                    if (pn == "ParameterName")
                        paramName = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    else if (pn == "DefaultValue")
                    {
                        var val = GetPropertyValue(prop, 0);
                        // Use FormatValue for structs (like LinearColor/Vector)
                        paramValue = FormatValue(val);
                    }
                    else if (pn == "Group")
                        paramGroup = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    else if (pn == "Texture" && prop is ObjectPropertyData texProp && texProp.Value.Index != 0)
                    {
                        var texName = ResolvePackageIndex(asset, texProp.Value);
                        if (!string.IsNullOrEmpty(texName) && texName != "[null]")
                            texturesUsed.Add(texName);
                    }
                }

                var details = "";
                if (!string.IsNullOrEmpty(paramName))
                {
                    details = paramName;
                    if (!string.IsNullOrEmpty(paramValue))
                        details += $"={paramValue}";
                    if (!string.IsNullOrEmpty(paramGroup))
                        details += $" [{paramGroup}]";
                }

                expressionNodes.Add((nodeType, expName, details));
            }
            else
            {
                expressionNodes.Add((nodeType, expName, ""));
            }
        }

        // Output expression nodes (group by type)
        if (expressionNodes.Count > 0)
        {
            xml.AppendLine("  <expressions>");

            // Parameters first
            var paramNodes = expressionNodes.Where(n =>
                n.type.Contains("Parameter") ||
                n.type.Contains("TextureSample")).ToList();

            foreach (var (type, name, details) in paramNodes)
            {
                var detailAttr = !string.IsNullOrEmpty(details) ? $" details=\"{EscapeXml(details)}\"" : "";
                xml.AppendLine($"    <node type=\"{EscapeXml(type)}\"{detailAttr} />");
            }

            // Then count of other node types
            var otherNodes = expressionNodes.Where(n =>
                !n.type.Contains("Parameter") &&
                !n.type.Contains("TextureSample")).ToList();

            var nodeCounts = otherNodes.GroupBy(n => n.type)
                .Select(g => (g.Key, g.Count()))
                .OrderByDescending(x => x.Item2);

            foreach (var (type, count) in nodeCounts)
            {
                xml.AppendLine($"    <node type=\"{EscapeXml(type)}\" count=\"{count}\" />");
            }

            xml.AppendLine("  </expressions>");
        }

        // Output textures used
        if (texturesUsed.Count > 0)
        {
            xml.AppendLine("  <textures>");
            foreach (var tex in texturesUsed.OrderBy(t => t))
            {
                xml.AppendLine($"    <texture ref=\"{EscapeXml(tex)}\" />");
            }
            xml.AppendLine("  </textures>");
        }
    }

    xml.AppendLine(isInstance ? "</material-instance>" : "</material>");

    Console.WriteLine(xml.ToString());
}

void ExtractScalarParametersXml(ArrayPropertyData arrayProp, List<(string name, object value, string group)> output)
{
    if (arrayProp.Value == null) return;

    foreach (var item in arrayProp.Value)
    {
        if (item is StructPropertyData structProp && structProp.Value != null)
        {
            string name = "";
            object value = 0;
            string group = "";

            foreach (var field in structProp.Value)
            {
                var fieldName = field.Name.ToString();
                if (fieldName == "ParameterName" || fieldName == "Name")
                    name = GetPropertyValue(field, 0)?.ToString() ?? "";
                else if (fieldName == "ParameterValue" || fieldName == "Value")
                    value = GetPropertyValue(field, 0) ?? 0;
                else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                {
                    foreach (var infoField in infoStruct.Value)
                    {
                        if (infoField.Name.ToString() == "Name")
                            name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                        else if (infoField.Name.ToString() == "Group")
                            group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                    }
                }
            }

            if (!string.IsNullOrEmpty(name))
                output.Add((name, value, group));
        }
    }
}

void ExtractVectorParametersXml(ArrayPropertyData arrayProp, List<(string name, List<object> value, string group)> output)
{
    if (arrayProp.Value == null) return;

    foreach (var item in arrayProp.Value)
    {
        if (item is StructPropertyData structProp && structProp.Value != null)
        {
            string name = "";
            var value = new List<object> { 0, 0, 0, 1 };
            string group = "";

            foreach (var field in structProp.Value)
            {
                var fieldName = field.Name.ToString();
                if (fieldName == "ParameterName" || fieldName == "Name")
                    name = GetPropertyValue(field, 0)?.ToString() ?? "";
                else if (fieldName == "ParameterValue" || fieldName == "Value")
                {
                    var val = GetPropertyValue(field, 1);
                    if (val is Dictionary<string, object> colorDict)
                    {
                        var r = colorDict.GetValueOrDefault("R", colorDict.GetValueOrDefault("r", 0));
                        var g = colorDict.GetValueOrDefault("G", colorDict.GetValueOrDefault("g", 0));
                        var b = colorDict.GetValueOrDefault("B", colorDict.GetValueOrDefault("b", 0));
                        var a = colorDict.GetValueOrDefault("A", colorDict.GetValueOrDefault("a", 1));
                        value = new List<object> { r, g, b, a };
                    }
                }
                else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                {
                    foreach (var infoField in infoStruct.Value)
                    {
                        if (infoField.Name.ToString() == "Name")
                            name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                        else if (infoField.Name.ToString() == "Group")
                            group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                    }
                }
            }

            if (!string.IsNullOrEmpty(name))
                output.Add((name, value, group));
        }
    }
}

void ExtractTextureParametersXml(UAsset asset, ArrayPropertyData arrayProp, List<(string name, string texture, string group)> output)
{
    if (arrayProp.Value == null) return;

    foreach (var item in arrayProp.Value)
    {
        if (item is StructPropertyData structProp && structProp.Value != null)
        {
            string name = "";
            string texture = "";
            string group = "";

            foreach (var field in structProp.Value)
            {
                var fieldName = field.Name.ToString();
                if (fieldName == "ParameterName" || fieldName == "Name")
                    name = GetPropertyValue(field, 0)?.ToString() ?? "";
                else if (fieldName == "ParameterValue" || fieldName == "Value")
                {
                    if (field is ObjectPropertyData objProp)
                        texture = ResolvePackageIndex(asset, objProp.Value);
                    else if (field is SoftObjectPropertyData softProp)
                        texture = softProp.Value.ToString();
                    else
                        texture = GetPropertyValue(field, 0)?.ToString() ?? "";
                }
                else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                {
                    foreach (var infoField in infoStruct.Value)
                    {
                        if (infoField.Name.ToString() == "Name")
                            name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                        else if (infoField.Name.ToString() == "Group")
                            group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                    }
                }
            }

            if (!string.IsNullOrEmpty(name))
                output.Add((name, texture, group));
        }
    }
}

void ExtractStaticSwitchesXml(StructPropertyData staticStruct, List<(string name, bool value)> output)
{
    if (staticStruct.Value == null) return;

    foreach (var field in staticStruct.Value)
    {
        var fieldName = field.Name.ToString();
        if (fieldName.Contains("Switch") && field is ArrayPropertyData switchArray && switchArray.Value != null)
        {
            foreach (var sw in switchArray.Value)
            {
                if (sw is StructPropertyData swStruct && swStruct.Value != null)
                {
                    string name = "";
                    bool value = false;

                    foreach (var swField in swStruct.Value)
                    {
                        var swFieldName = swField.Name.ToString();
                        if (swFieldName == "ParameterName" || swFieldName == "Name")
                            name = GetPropertyValue(swField, 0)?.ToString() ?? "";
                        else if (swFieldName == "Value" || swFieldName == "ParameterValue")
                            value = GetPropertyValue(swField, 0) as bool? ?? false;
                        else if (swFieldName == "ParameterInfo" && swField is StructPropertyData infoStruct && infoStruct.Value != null)
                        {
                            foreach (var infoField in infoStruct.Value)
                            {
                                if (infoField.Name.ToString() == "Name")
                                    name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                            }
                        }
                    }

                    if (!string.IsNullOrEmpty(name))
                        output.Add((name, value));
                }
            }
        }
    }
}

// ============================================================================
// MATERIAL FUNCTION - Extract MaterialFunction inputs, outputs, and parameters
// ============================================================================
void ExtractMaterialFunction(UAsset asset)
{
    var xml = new System.Text.StringBuilder();

    // Find the main MaterialFunction export
    var mfExport = asset.Exports
        .OfType<NormalExport>()
        .FirstOrDefault(e => e.GetExportClassType()?.ToString() == "MaterialFunction");

    if (mfExport == null)
    {
        xml.AppendLine("<material-function>");
        xml.AppendLine("  <error>No MaterialFunction found in asset</error>");
        xml.AppendLine("</material-function>");
        Console.WriteLine(xml.ToString());
        return;
    }

    var mfName = mfExport.ObjectName.ToString();

    // Collect inputs from MaterialExpressionFunctionInput exports
    var inputs = new List<(string name, string type, int priority)>();
    foreach (var export in asset.Exports.OfType<NormalExport>())
    {
        if (export.GetExportClassType()?.ToString() == "MaterialExpressionFunctionInput" && export.Data != null)
        {
            string inputName = "Input";
            string inputType = "Vector3";  // Default type
            int sortPriority = 0;

            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "InputName")
                    inputName = GetPropertyValue(prop, 0)?.ToString() ?? "Input";
                else if (propName == "InputType" && prop is BytePropertyData byteProp)
                {
                    // Map InputType enum values to readable names
                    var typeVal = byteProp.Value;
                    inputType = typeVal switch
                    {
                        0 => "Scalar",
                        1 => "Vector2",
                        2 => "Vector3",
                        3 => "Vector4",
                        4 => "Texture2D",
                        5 => "TextureCube",
                        6 => "Texture2DArray",
                        7 => "VolumeTexture",
                        8 => "StaticBool",
                        9 => "MaterialAttributes",
                        10 => "External",
                        _ => $"Type{typeVal}"
                    };
                }
                else if (propName == "SortPriority")
                    sortPriority = (int)(GetPropertyValue(prop, 0) ?? 0);
            }
            inputs.Add((inputName, inputType, sortPriority));
        }
    }

    // Collect outputs from MaterialExpressionFunctionOutput exports
    var outputs = new List<(string name, int priority)>();
    foreach (var export in asset.Exports.OfType<NormalExport>())
    {
        if (export.GetExportClassType()?.ToString() == "MaterialExpressionFunctionOutput" && export.Data != null)
        {
            string outputName = "Result";  // Default name
            int sortPriority = 0;

            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "OutputName")
                    outputName = GetPropertyValue(prop, 0)?.ToString() ?? "Result";
                else if (propName == "SortPriority")
                    sortPriority = (int)(GetPropertyValue(prop, 0) ?? 0);
            }
            outputs.Add((outputName, sortPriority));
        }
    }

    // Collect scalar parameters
    var scalarParams = new List<(string name, float defaultVal, string group)>();
    foreach (var export in asset.Exports.OfType<NormalExport>())
    {
        if (export.GetExportClassType()?.ToString() == "MaterialExpressionScalarParameter" && export.Data != null)
        {
            string paramName = "Parameter";
            float defaultVal = 0f;
            string group = "";

            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "ParameterName")
                    paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                else if (propName == "DefaultValue" && prop is FloatPropertyData floatProp)
                    defaultVal = floatProp.Value;
                else if (propName == "Group")
                    group = GetPropertyValue(prop, 0)?.ToString() ?? "";
            }
            scalarParams.Add((paramName, defaultVal, group));
        }
    }

    // Collect vector parameters
    var vectorParams = new List<(string name, string defaultVal, string group)>();
    foreach (var export in asset.Exports.OfType<NormalExport>())
    {
        if (export.GetExportClassType()?.ToString() == "MaterialExpressionVectorParameter" && export.Data != null)
        {
            string paramName = "Parameter";
            string defaultVal = "0,0,0,1";
            string group = "";

            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "ParameterName")
                    paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                else if (propName == "DefaultValue" && prop is StructPropertyData)
                {
                    // LinearColor struct - extract RGBA values
                    var colorData = GetPropertyValue(prop, 1);
                    if (colorData is Dictionary<string, object> colorDict)
                    {
                        // Check for R,G,B,A keys first (standard struct format)
                        if (colorDict.ContainsKey("R") || colorDict.ContainsKey("r"))
                        {
                            var r = colorDict.GetValueOrDefault("R", colorDict.GetValueOrDefault("r", 0));
                            var g = colorDict.GetValueOrDefault("G", colorDict.GetValueOrDefault("g", 0));
                            var b = colorDict.GetValueOrDefault("B", colorDict.GetValueOrDefault("b", 0));
                            var a = colorDict.GetValueOrDefault("A", colorDict.GetValueOrDefault("a", 1));
                            defaultVal = $"{r},{g},{b},{a}";
                        }
                        // LinearColor stores value as a string "(R, G, B, A)" in DefaultValue property
                        else if (colorDict.TryGetValue("DefaultValue", out var colorStr) && colorStr is string cs)
                        {
                            // Parse "(R, G, B, A)" format and reformat as "R,G,B,A"
                            if (cs.StartsWith("(") && cs.EndsWith(")"))
                            {
                                defaultVal = cs.Trim('(', ')').Replace(" ", "");
                            }
                        }
                    }
                }
                else if (propName == "Group")
                    group = GetPropertyValue(prop, 0)?.ToString() ?? "";
            }
            vectorParams.Add((paramName, defaultVal, group));
        }
    }

    // Collect static switch parameters
    var switchParams = new List<(string name, bool defaultVal, string group)>();
    foreach (var export in asset.Exports.OfType<NormalExport>())
    {
        if (export.GetExportClassType()?.ToString() == "MaterialExpressionStaticSwitchParameter" && export.Data != null)
        {
            string paramName = "Parameter";
            bool defaultVal = false;
            string group = "";

            foreach (var prop in export.Data)
            {
                var propName = prop.Name.ToString();
                if (propName == "ParameterName")
                    paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                else if (propName == "DefaultValue" && prop is BoolPropertyData boolProp)
                    defaultVal = boolProp.Value;
                else if (propName == "Group")
                    group = GetPropertyValue(prop, 0)?.ToString() ?? "";
            }
            switchParams.Add((paramName, defaultVal, group));
        }
    }

    // Output XML
    xml.AppendLine("<material-function>");
    xml.AppendLine($"  <name>{EscapeXml(mfName)}</name>");

    // Inputs section (sorted by priority)
    if (inputs.Count > 0)
    {
        xml.AppendLine("  <inputs>");
        foreach (var (name, type, priority) in inputs.OrderBy(i => i.priority))
        {
            xml.AppendLine($"    <input name=\"{EscapeXml(name)}\" type=\"{EscapeXml(type)}\" priority=\"{priority}\" />");
        }
        xml.AppendLine("  </inputs>");
    }

    // Outputs section (sorted by priority)
    if (outputs.Count > 0)
    {
        xml.AppendLine("  <outputs>");
        foreach (var (name, priority) in outputs.OrderBy(o => o.priority))
        {
            xml.AppendLine($"    <output name=\"{EscapeXml(name)}\" priority=\"{priority}\" />");
        }
        xml.AppendLine("  </outputs>");
    }

    // Parameters section
    if (scalarParams.Count > 0 || vectorParams.Count > 0 || switchParams.Count > 0)
    {
        xml.AppendLine("  <parameters>");

        foreach (var (name, defaultVal, group) in scalarParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            xml.AppendLine($"    <scalar name=\"{EscapeXml(name)}\" default=\"{defaultVal}\"{groupAttr} />");
        }

        foreach (var (name, defaultVal, group) in vectorParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            xml.AppendLine($"    <vector name=\"{EscapeXml(name)}\" default=\"{defaultVal}\"{groupAttr} />");
        }

        foreach (var (name, defaultVal, group) in switchParams)
        {
            var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
            xml.AppendLine($"    <switch name=\"{EscapeXml(name)}\" default=\"{defaultVal.ToString().ToLower()}\"{groupAttr} />");
        }

        xml.AppendLine("  </parameters>");
    }

    xml.AppendLine("</material-function>");

    Console.WriteLine(xml.ToString());
}

// ============================================================================
// REFERENCES - Extract all asset references
// ============================================================================
void ExtractReferences(UAsset asset)
{
    var xml = new System.Text.StringBuilder();
    xml.AppendLine("<references>");

    // Get the main asset name
    var mainExport = asset.Exports.FirstOrDefault();
    var assetName = mainExport?.ObjectName.ToString() ?? "Unknown";
    xml.AppendLine($"  <asset>{EscapeXml(assetName)}</asset>");

    // Collect unique asset references from imports
    var assetRefs = new HashSet<string>();
    var classRefs = new HashSet<string>();
    var scriptRefs = new HashSet<string>();

    foreach (var import in asset.Imports)
    {
        var objectName = import.ObjectName.ToString();
        var className = import.ClassName.ToString();
        var outerName = import.OuterIndex.Index == 0 ? "" :
            (import.OuterIndex.IsImport() ? asset.Imports[-import.OuterIndex.Index - 1].ObjectName.ToString() : "");

        // Skip internal engine references
        if (objectName.StartsWith("Default__")) continue;
        if (className == "Package" && !objectName.Contains("/Game/")) continue;

        // Build the full path if it's a game asset
        string fullPath = "";

        // Check if this import or its outer is a Package with /Game/ path
        if (className == "Package" && objectName.Contains("/Game/"))
        {
            // This is a direct package reference
            fullPath = objectName;
        }
        else if (!string.IsNullOrEmpty(outerName) && outerName.Contains("/Game/"))
        {
            // The outer is the package path
            fullPath = outerName;
        }
        else
        {
            // Try to find the package in the outer chain
            var currentIdx = import.OuterIndex;
            while (currentIdx.Index != 0)
            {
                if (currentIdx.IsImport())
                {
                    var outerImport = asset.Imports[-currentIdx.Index - 1];
                    if (outerImport.ClassName.ToString() == "Package")
                    {
                        var pkgName = outerImport.ObjectName.ToString();
                        if (pkgName.Contains("/Game/") || pkgName.StartsWith("/Script/"))
                        {
                            fullPath = pkgName;
                            break;
                        }
                    }
                    currentIdx = outerImport.OuterIndex;
                }
                else
                {
                    break;
                }
            }
        }

        // Categorize the reference
        if (!string.IsNullOrEmpty(fullPath))
        {
            if (fullPath.StartsWith("/Game/"))
            {
                assetRefs.Add(fullPath);
            }
            else if (fullPath.StartsWith("/Script/"))
            {
                scriptRefs.Add(fullPath);
            }
        }

        // Track class/type references (for inheritance, interfaces, etc.)
        if (className == "Class" || className == "BlueprintGeneratedClass" || className == "WidgetBlueprintGeneratedClass")
        {
            classRefs.Add(objectName);
        }
    }

    // Also scan exports for ObjectProperty references
    foreach (var export in asset.Exports)
    {
        if (export is NormalExport normalExport && normalExport.Data != null)
        {
            foreach (var prop in normalExport.Data)
            {
                CollectAssetRefsFromProperty(asset, prop, assetRefs);
            }
        }
    }

    // Output asset references (other game assets this depends on)
    if (assetRefs.Count > 0)
    {
        xml.AppendLine("  <asset-refs>");
        foreach (var refPath in assetRefs.OrderBy(r => r))
        {
            xml.AppendLine($"    <ref>{EscapeXml(refPath)}</ref>");
        }
        xml.AppendLine("  </asset-refs>");
    }

    // Output class references (parent classes, interfaces)
    if (classRefs.Count > 0)
    {
        xml.AppendLine("  <class-refs>");
        foreach (var classRef in classRefs.OrderBy(r => r))
        {
            xml.AppendLine($"    <ref>{EscapeXml(classRef)}</ref>");
        }
        xml.AppendLine("  </class-refs>");
    }

    // Output script/engine references
    if (scriptRefs.Count > 0)
    {
        xml.AppendLine("  <script-refs>");
        foreach (var scriptRef in scriptRefs.OrderBy(r => r))
        {
            xml.AppendLine($"    <ref>{EscapeXml(scriptRef)}</ref>");
        }
        xml.AppendLine("  </script-refs>");
    }

    xml.AppendLine("</references>");
    Console.WriteLine(xml.ToString());
}

void CollectAssetRefsFromProperty(UAsset asset, PropertyData prop, HashSet<string> assetRefs)
{
    switch (prop)
    {
        case ObjectPropertyData objProp:
            var refPath = ResolveAssetPath(asset, objProp.Value);
            if (!string.IsNullOrEmpty(refPath) && refPath.StartsWith("/Game/"))
                assetRefs.Add(refPath);
            break;

        case SoftObjectPropertyData softProp:
            var packageName = softProp.Value.AssetPath.PackageName;
            if (packageName != null)
            {
                var softPath = packageName.ToString();
                if (!string.IsNullOrEmpty(softPath) && softPath.StartsWith("/Game/"))
                    assetRefs.Add(softPath);
            }
            break;

        case ArrayPropertyData arrayProp:
            if (arrayProp.Value != null)
            {
                foreach (var item in arrayProp.Value)
                    CollectAssetRefsFromProperty(asset, item, assetRefs);
            }
            break;

        case StructPropertyData structProp:
            if (structProp.Value != null)
            {
                foreach (var field in structProp.Value)
                    CollectAssetRefsFromProperty(asset, field, assetRefs);
            }
            break;

        case MapPropertyData mapProp:
            if (mapProp.Value != null)
            {
                foreach (var kvp in mapProp.Value)
                {
                    CollectAssetRefsFromProperty(asset, kvp.Key, assetRefs);
                    CollectAssetRefsFromProperty(asset, kvp.Value, assetRefs);
                }
            }
            break;
    }
}

string ResolveAssetPath(UAsset asset, FPackageIndex index)
{
    if (index == null || index.Index == 0) return "";

    try
    {
        if (index.IsImport())
        {
            var import = index.ToImport(asset);
            if (import == null) return "";

            // Walk up the outer chain to find the package
            var currentIdx = import.OuterIndex;
            while (currentIdx.Index != 0)
            {
                if (currentIdx.IsImport())
                {
                    var outerImport = asset.Imports[-currentIdx.Index - 1];
                    if (outerImport.ClassName.ToString() == "Package")
                    {
                        var pkgName = outerImport.ObjectName.ToString();
                        if (pkgName.StartsWith("/Game/"))
                            return pkgName;
                    }
                    currentIdx = outerImport.OuterIndex;
                }
                else
                {
                    break;
                }
            }

            // Check if the import itself is from a game package
            if (import.ClassName.ToString() == "Package")
            {
                var pkgName = import.ObjectName.ToString();
                if (pkgName.StartsWith("/Game/"))
                    return pkgName;
            }
        }
        else if (index.IsExport())
        {
            // Same asset reference - not needed for cross-asset refs
            return "";
        }
    }
    catch
    {
        // Fall through
    }

    return "";
}

// ============================================================================
// BYTECODE ANALYSIS
// ============================================================================
void AnalyzeExpression(UAsset asset, KismetExpression expr,
    HashSet<string> calls, HashSet<string> variables, HashSet<string> casts)
{
    if (expr == null) return;

    switch (expr)
    {
        // Function calls - subclasses must come before parent classes
        // EX_LocalFinalFunction and EX_CallMath extend EX_FinalFunction
        case EX_LocalFinalFunction localFunc:
            calls.Add(ResolvePackageIndex(asset, localFunc.StackNode));
            AnalyzeParameters(asset, localFunc.Parameters, calls, variables, casts);
            break;

        case EX_CallMath mathFunc:
            calls.Add(ResolvePackageIndex(asset, mathFunc.StackNode));
            AnalyzeParameters(asset, mathFunc.Parameters, calls, variables, casts);
            break;

        case EX_FinalFunction finalFunc:
            calls.Add(ResolvePackageIndex(asset, finalFunc.StackNode));
            AnalyzeParameters(asset, finalFunc.Parameters, calls, variables, casts);
            break;

        // EX_LocalVirtualFunction extends EX_VirtualFunction
        case EX_LocalVirtualFunction localVirtFunc:
            calls.Add(localVirtFunc.VirtualFunctionName.ToString());
            AnalyzeParameters(asset, localVirtFunc.Parameters, calls, variables, casts);
            break;

        case EX_VirtualFunction virtFunc:
            calls.Add(virtFunc.VirtualFunctionName.ToString());
            AnalyzeParameters(asset, virtFunc.Parameters, calls, variables, casts);
            break;

        // Variable access
        case EX_InstanceVariable instVar:
            variables.Add(ResolvePropertyPointer(asset, instVar.Variable));
            break;

        case EX_LocalVariable localVar:
            variables.Add(ResolvePropertyPointer(asset, localVar.Variable));
            break;

        case EX_LocalOutVariable localOutVar:
            variables.Add(ResolvePropertyPointer(asset, localOutVar.Variable));
            break;

        case EX_DefaultVariable defaultVar:
            variables.Add(ResolvePropertyPointer(asset, defaultVar.Variable));
            break;

        // Delegates
        case EX_InstanceDelegate instDelegate:
            calls.Add(instDelegate.FunctionName.ToString());
            break;

        // Casts (EX_CastBase provides ClassPtr and Target)
        case EX_DynamicCast dynCast:
            casts.Add(ResolvePackageIndex(asset, dynCast.ClassPtr));
            if (dynCast.Target != null)
                AnalyzeExpression(asset, dynCast.Target, calls, variables, casts);
            break;

        case EX_MetaCast metaCast:
            casts.Add(ResolvePackageIndex(asset, metaCast.ClassPtr));
            if (metaCast.Target != null)
                AnalyzeExpression(asset, metaCast.Target, calls, variables, casts);
            break;

        // Context expressions (subclass first to avoid unreachable code)
        case EX_Context_FailSilent contextFail:
            if (contextFail.ObjectExpression != null)
                AnalyzeExpression(asset, contextFail.ObjectExpression, calls, variables, casts);
            if (contextFail.ContextExpression != null)
                AnalyzeExpression(asset, contextFail.ContextExpression, calls, variables, casts);
            break;

        case EX_Context context:
            if (context.ObjectExpression != null)
                AnalyzeExpression(asset, context.ObjectExpression, calls, variables, casts);
            if (context.ContextExpression != null)
                AnalyzeExpression(asset, context.ContextExpression, calls, variables, casts);
            break;

        // Let expressions (subclasses extend EX_LetBase with VariableExpression/AssignmentExpression)
        // EX_LetBase subclasses must come before EX_Let (which has different structure)
        case EX_LetObj letObj:
            if (letObj.VariableExpression != null)
                AnalyzeExpression(asset, letObj.VariableExpression, calls, variables, casts);
            if (letObj.AssignmentExpression != null)
                AnalyzeExpression(asset, letObj.AssignmentExpression, calls, variables, casts);
            break;

        case EX_LetBool letBool:
            if (letBool.VariableExpression != null)
                AnalyzeExpression(asset, letBool.VariableExpression, calls, variables, casts);
            if (letBool.AssignmentExpression != null)
                AnalyzeExpression(asset, letBool.AssignmentExpression, calls, variables, casts);
            break;

        case EX_LetDelegate letDelegate:
            if (letDelegate.VariableExpression != null)
                AnalyzeExpression(asset, letDelegate.VariableExpression, calls, variables, casts);
            if (letDelegate.AssignmentExpression != null)
                AnalyzeExpression(asset, letDelegate.AssignmentExpression, calls, variables, casts);
            break;

        case EX_LetMulticastDelegate letMulti:
            if (letMulti.VariableExpression != null)
                AnalyzeExpression(asset, letMulti.VariableExpression, calls, variables, casts);
            if (letMulti.AssignmentExpression != null)
                AnalyzeExpression(asset, letMulti.AssignmentExpression, calls, variables, casts);
            break;

        // EX_Let has different structure: Value (KismetPropertyPointer), Variable (expression), Expression (expression)
        case EX_Let letExpr:
            if (letExpr.Value != null)
                variables.Add(ResolvePropertyPointer(asset, letExpr.Value));
            if (letExpr.Variable != null)
                AnalyzeExpression(asset, letExpr.Variable, calls, variables, casts);
            if (letExpr.Expression != null)
                AnalyzeExpression(asset, letExpr.Expression, calls, variables, casts);
            break;

        case EX_StructMemberContext structMember:
            if (structMember.StructExpression != null)
                AnalyzeExpression(asset, structMember.StructExpression, calls, variables, casts);
            if (structMember.StructMemberExpression != null)
                variables.Add(ResolvePropertyPointer(asset, structMember.StructMemberExpression));
            break;

        case EX_ArrayGetByRef arrayGet:
            if (arrayGet.ArrayVariable != null)
                AnalyzeExpression(asset, arrayGet.ArrayVariable, calls, variables, casts);
            if (arrayGet.ArrayIndex != null)
                AnalyzeExpression(asset, arrayGet.ArrayIndex, calls, variables, casts);
            break;

        case EX_Return returnExpr:
            if (returnExpr.ReturnExpression != null)
                AnalyzeExpression(asset, returnExpr.ReturnExpression, calls, variables, casts);
            break;

        case EX_JumpIfNot jumpIfNot:
            if (jumpIfNot.BooleanExpression != null)
                AnalyzeExpression(asset, jumpIfNot.BooleanExpression, calls, variables, casts);
            break;

        case EX_Assert assertExpr:
            if (assertExpr.AssertExpression != null)
                AnalyzeExpression(asset, assertExpr.AssertExpression, calls, variables, casts);
            break;

        case EX_SetArray setArray:
            if (setArray.AssigningProperty != null)
                AnalyzeExpression(asset, setArray.AssigningProperty, calls, variables, casts);
            if (setArray.Elements != null)
            {
                foreach (var elem in setArray.Elements)
                    AnalyzeExpression(asset, elem, calls, variables, casts);
            }
            break;

        case EX_SetSet setSet:
            if (setSet.SetProperty != null)
                AnalyzeExpression(asset, setSet.SetProperty, calls, variables, casts);
            if (setSet.Elements != null)
            {
                foreach (var elem in setSet.Elements)
                    AnalyzeExpression(asset, elem, calls, variables, casts);
            }
            break;

        case EX_SetMap setMap:
            if (setMap.MapProperty != null)
                AnalyzeExpression(asset, setMap.MapProperty, calls, variables, casts);
            if (setMap.Elements != null)
            {
                foreach (var elem in setMap.Elements)
                    AnalyzeExpression(asset, elem, calls, variables, casts);
            }
            break;

        case EX_SwitchValue switchVal:
            if (switchVal.IndexTerm != null)
                AnalyzeExpression(asset, switchVal.IndexTerm, calls, variables, casts);
            if (switchVal.DefaultTerm != null)
                AnalyzeExpression(asset, switchVal.DefaultTerm, calls, variables, casts);
            if (switchVal.Cases != null)
            {
                foreach (var c in switchVal.Cases)
                {
                    if (c.CaseIndexValueTerm != null)
                        AnalyzeExpression(asset, c.CaseIndexValueTerm, calls, variables, casts);
                    if (c.CaseTerm != null)
                        AnalyzeExpression(asset, c.CaseTerm, calls, variables, casts);
                }
            }
            break;

        case EX_BindDelegate bindDelegate:
            calls.Add(bindDelegate.FunctionName.ToString());
            if (bindDelegate.Delegate != null)
                AnalyzeExpression(asset, bindDelegate.Delegate, calls, variables, casts);
            if (bindDelegate.ObjectTerm != null)
                AnalyzeExpression(asset, bindDelegate.ObjectTerm, calls, variables, casts);
            break;

        case EX_AddMulticastDelegate addMulti:
            if (addMulti.Delegate != null)
                AnalyzeExpression(asset, addMulti.Delegate, calls, variables, casts);
            if (addMulti.DelegateToAdd != null)
                AnalyzeExpression(asset, addMulti.DelegateToAdd, calls, variables, casts);
            break;

        case EX_RemoveMulticastDelegate removeMulti:
            if (removeMulti.Delegate != null)
                AnalyzeExpression(asset, removeMulti.Delegate, calls, variables, casts);
            if (removeMulti.DelegateToAdd != null)
                AnalyzeExpression(asset, removeMulti.DelegateToAdd, calls, variables, casts);
            break;

        case EX_ClearMulticastDelegate clearMulti:
            if (clearMulti.DelegateToClear != null)
                AnalyzeExpression(asset, clearMulti.DelegateToClear, calls, variables, casts);
            break;

        case EX_InterfaceContext interfaceCtx:
            if (interfaceCtx.InterfaceValue != null)
                AnalyzeExpression(asset, interfaceCtx.InterfaceValue, calls, variables, casts);
            break;

        case EX_ObjectConst objConst:
            // Extract the constant object reference for context
            var objName = ResolvePackageIndex(asset, objConst.Value);
            if (!string.IsNullOrEmpty(objName) && objName != "[null]")
                variables.Add(objName);
            break;

        case EX_SoftObjectConst softObjConst:
            if (softObjConst.Value != null)
                AnalyzeExpression(asset, softObjConst.Value, calls, variables, casts);
            break;

        case EX_FieldPathConst fieldPathConst:
            if (fieldPathConst.Value != null)
                AnalyzeExpression(asset, fieldPathConst.Value, calls, variables, casts);
            break;

        case EX_PropertyConst propConst:
            variables.Add(ResolvePropertyPointer(asset, propConst.Property));
            break;
    }
}

void AnalyzeParameters(UAsset asset, KismetExpression[]? parameters,
    HashSet<string> calls, HashSet<string> variables, HashSet<string> casts)
{
    if (parameters == null) return;
    foreach (var param in parameters)
    {
        AnalyzeExpression(asset, param, calls, variables, casts);
    }
}

// ============================================================================
// CONTROL FLOW ANALYSIS - Extract control flow summary from bytecode
// ============================================================================
object AnalyzeControlFlow(KismetExpression[]? bytecode)
{
    if (bytecode == null || bytecode.Length == 0)
        return null;

    int branchCount = 0;
    int switchCount = 0;
    bool hasReturn = false;

    foreach (var expr in bytecode)
    {
        CountControlFlowExpressions(expr, ref branchCount, ref switchCount, ref hasReturn);
    }

    bool hasBranches = branchCount > 0 || switchCount > 0;

    // Determine complexity
    // Low: 0-2 branches, Medium: 3-5, High: 6+
    int totalBranches = branchCount + switchCount;
    string complexity = totalBranches switch
    {
        0 => "none",
        <= 2 => "low",
        <= 5 => "medium",
        _ => "high"
    };

    return new
    {
        has_branches = hasBranches,
        has_loops = false,  // Loop detection deferred - requires back-edge analysis
        branch_count = branchCount,
        switch_count = switchCount,
        complexity = complexity
    };
}

void CountControlFlowExpressions(KismetExpression expr, ref int branchCount, ref int switchCount, ref bool hasReturn)
{
    if (expr == null) return;

    switch (expr)
    {
        // Conditional branches
        case EX_JumpIfNot jumpIfNot:
            branchCount++;
            if (jumpIfNot.BooleanExpression != null)
                CountControlFlowExpressions(jumpIfNot.BooleanExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        // Switch statements
        case EX_SwitchValue switchVal:
            switchCount++;
            if (switchVal.IndexTerm != null)
                CountControlFlowExpressions(switchVal.IndexTerm, ref branchCount, ref switchCount, ref hasReturn);
            if (switchVal.DefaultTerm != null)
                CountControlFlowExpressions(switchVal.DefaultTerm, ref branchCount, ref switchCount, ref hasReturn);
            if (switchVal.Cases != null)
            {
                foreach (var c in switchVal.Cases)
                {
                    if (c.CaseIndexValueTerm != null)
                        CountControlFlowExpressions(c.CaseIndexValueTerm, ref branchCount, ref switchCount, ref hasReturn);
                    if (c.CaseTerm != null)
                        CountControlFlowExpressions(c.CaseTerm, ref branchCount, ref switchCount, ref hasReturn);
                }
            }
            break;

        // Return statements
        case EX_Return returnExpr:
            hasReturn = true;
            if (returnExpr.ReturnExpression != null)
                CountControlFlowExpressions(returnExpr.ReturnExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        // Recurse into nested expressions
        // Note: EX_Context_FailSilent extends EX_Context, so subclass must come first
        case EX_Context_FailSilent contextFail:
            if (contextFail.ContextExpression != null)
                CountControlFlowExpressions(contextFail.ContextExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        case EX_Context context:
            if (context.ContextExpression != null)
                CountControlFlowExpressions(context.ContextExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        case EX_Let letExpr:
            if (letExpr.Expression != null)
                CountControlFlowExpressions(letExpr.Expression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        case EX_LetObj letObj:
            if (letObj.AssignmentExpression != null)
                CountControlFlowExpressions(letObj.AssignmentExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;

        case EX_LetBool letBool:
            if (letBool.AssignmentExpression != null)
                CountControlFlowExpressions(letBool.AssignmentExpression, ref branchCount, ref switchCount, ref hasReturn);
            break;
    }
}

string ResolvePackageIndex(UAsset asset, FPackageIndex index)
{
    if (index == null || index.Index == 0) return "[null]";

    try
    {
        if (index.IsImport())
        {
            var import = index.ToImport(asset);
            return import?.ObjectName.ToString() ?? $"[import:{index.Index}]";
        }
        if (index.IsExport())
        {
            var export = index.ToExport(asset);
            return export?.ObjectName.ToString() ?? $"[export:{index.Index}]";
        }
    }
    catch
    {
        // Fall through to unknown
    }

    return $"[unknown:{index.Index}]";
}

string ResolvePropertyPointer(UAsset asset, KismetPropertyPointer? ptr)
{
    if (ptr == null) return "[null]";

    try
    {
        // UE5+ uses FFieldPath (New property)
        if (ptr.New?.Path != null && ptr.New.Path.Length > 0)
        {
            return string.Join(".", ptr.New.Path.Select(p => p.ToString()));
        }

        // UE4 uses FPackageIndex (Old property)
        if (ptr.Old != null && ptr.Old.Index != 0)
        {
            return ResolvePackageIndex(asset, ptr.Old);
        }
    }
    catch
    {
        // Fall through to unknown
    }

    return "[unknown]";
}

// ============================================================================
// HELPERS
// ============================================================================

/// <summary>
/// Resolve an FPackageIndex (ObjectProperty value) to a human-readable path or class reference.
/// - Imports: walk outer chain to find the package path. For /Script/ imports, returns
///   tuple format "(, /Script/Module.ClassName, )" so Python's _extract_path_from_ref works.
///   For all other / paths (plugin mounts like /ShooterCore/, /Game/, etc.), returns the package path.
/// - Exports: returns the export's ObjectName (local reference within the same asset).
/// - Index 0: returns null.
/// </summary>
object? ResolveObjectRef(FPackageIndex index)
{
    if (index == null || index.Index == 0) return null;
    if (currentAsset == null) return index.Index;

    try
    {
        if (index.IsImport())
        {
            var import = index.ToImport(currentAsset);
            if (import == null) return index.Index;

            var objectName = import.ObjectName.ToString();

            // Walk up the outer chain to find the package
            var currentIdx = import.OuterIndex;
            while (currentIdx.Index != 0)
            {
                if (currentIdx.IsImport())
                {
                    var outerImport = currentAsset.Imports[-currentIdx.Index - 1];
                    if (outerImport.ClassName.ToString() == "Package")
                    {
                        var pkgName = outerImport.ObjectName.ToString();
                        if (pkgName.StartsWith("/Script/"))
                        {
                            // Return tuple format for class references
                            return $"(, {pkgName}.{objectName}, )";
                        }
                        if (pkgName.StartsWith("/"))
                        {
                            // Plugin mounts, /Game/, etc. — return asset path
                            return pkgName;
                        }
                    }
                    currentIdx = outerImport.OuterIndex;
                }
                else
                {
                    break;
                }
            }

            // Check if the import itself is a Package
            if (import.ClassName.ToString() == "Package")
            {
                var pkgName = import.ObjectName.ToString();
                if (pkgName.StartsWith("/Script/"))
                    return $"(, {pkgName}, )";
                if (pkgName.StartsWith("/"))
                    return pkgName;
            }

            // Couldn't resolve to a path — return the object name as fallback
            return objectName;
        }
        else if (index.IsExport())
        {
            var export = index.ToExport(currentAsset);
            return export?.ObjectName.ToString() ?? (object)index.Index;
        }
    }
    catch
    {
        // Fall through
    }

    return index.Index;
}

string GetTextPropertyValue(TextPropertyData textProp)
{
    // Prefer CultureInvariantString (the actual source text) for Base/None history types.
    // textProp.Value is often just the 32-char hex localization key.
    var culture = textProp.CultureInvariantString?.ToString();
    if (!string.IsNullOrEmpty(culture))
        return culture;

    var val = textProp.Value?.ToString();
    if (string.IsNullOrEmpty(val))
        return "";

    // Filter out 32-char hex localization keys (e.g. "A1B2C3D4E5F6...")
    if (System.Text.RegularExpressions.Regex.IsMatch(val, @"^[0-9A-Fa-f]{32}$"))
        return "";

    return val;
}

object GetPropertyValue(PropertyData prop, int depth)
{
    if (depth > 3) return "[max depth]";

    try
    {
        return prop switch
        {
            StrPropertyData strProp => strProp.Value?.ToString() ?? "",
            TextPropertyData textProp => GetTextPropertyValue(textProp),
            IntPropertyData intProp => intProp.Value,
            Int64PropertyData int64Prop => int64Prop.Value,
            FloatPropertyData floatProp => floatProp.Value,
            DoublePropertyData doubleProp => doubleProp.Value,
            BoolPropertyData boolProp => boolProp.Value,
            NamePropertyData nameProp => nameProp.Value.ToString(),
            ObjectPropertyData objProp => ResolveObjectRef(objProp.Value) ?? (object)"null",
            SoftObjectPropertyData softProp => softProp.Value.ToString(),
            EnumPropertyData enumProp => enumProp.Value.ToString(),
            BytePropertyData byteProp => byteProp.Value.ToString(),

            StructPropertyData structProp => ExtractStruct(structProp, depth),
            GameplayTagContainerPropertyData tagContainer => ExtractGameplayTagContainer(tagContainer),
            SetPropertyData setProp => $"[Set: {(setProp.Value != null ? setProp.Value.Length : 0)} items]",
            ArrayPropertyData arrayProp => ExtractArray(arrayProp, depth),
            MapPropertyData mapProp => $"[Map: {(mapProp.Value != null ? mapProp.Value.Count : 0)} entries]",

            _ => prop.ToString() ?? "[unknown]"
        };
    }
    catch
    {
        return "[error]";
    }
}

object ExtractStruct(StructPropertyData structProp, int depth)
{
    if (structProp.Value == null || structProp.Value.Count == 0)
        return $"[Struct: {structProp.StructType}]";

    var structData = new Dictionary<string, object>
    {
        ["_type"] = structProp.StructType?.ToString() ?? "Unknown"
    };

    foreach (var prop in structProp.Value)
    {
        structData[prop.Name.ToString()] = GetPropertyValue(prop, depth + 1);
    }

    return structData;
}

object ExtractArray(ArrayPropertyData arrayProp, int depth)
{
    if (arrayProp.Value == null || arrayProp.Value.Length == 0)
        return new List<object>();

    // For large arrays, just return count
    if (arrayProp.Value.Length > 20)
        return $"[Array: {arrayProp.Value.Length} items]";

    var items = new List<object>();
    foreach (var item in arrayProp.Value)
    {
        items.Add(GetPropertyValue(item, depth + 1));
    }
    return items;
}

object ExtractGameplayTagContainer(GameplayTagContainerPropertyData tagContainer)
{
    var tags = new List<string>();
    if (tagContainer.Value != null)
    {
        foreach (var tag in tagContainer.Value)
        {
            var tagStr = tag.ToString();
            if (!string.IsNullOrEmpty(tagStr) && tagStr != "None")
            {
                tags.Add(tagStr);
            }
        }
    }
    return new Dictionary<string, object>
    {
        ["_type"] = "GameplayTagContainer",
        ["tags"] = tags
    };
}

// ============================================================================
// BATCH OPERATIONS - For high-performance indexing (430x speedup)
// ============================================================================

int ResolveMaxParallelism(int fallback)
{
    var maxCpu = Math.Max(1, Environment.ProcessorCount);
    var overrideRaw = Environment.GetEnvironmentVariable("UE_ASSETPARSER_MAX_PARALLELISM");
    if (int.TryParse(overrideRaw, out var parsed) && parsed > 0)
    {
        return Math.Min(parsed, maxCpu);
    }
    if (fallback <= 0)
    {
        return maxCpu;
    }
    return Math.Min(fallback, maxCpu);
}

// Ultra-fast header-only parsing (10-100x faster than full UAssetAPI)
// Only reads magic number and file size, detects type from filename
void BatchFastSummary(List<string> paths)
{
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new {
                    path = path,
                    error = "File not found"
                }));
                return;
            }

            // Just read magic to verify it's a valid .uasset
            using var fs = new FileStream(resolvedPath, FileMode.Open, FileAccess.Read, FileShare.Read, 4096, FileOptions.SequentialScan);
            using var reader = new BinaryReader(fs);

            var magic = reader.ReadUInt32();
            if (magic != 0x9E2A83C1)
            {
                results.Add(JsonSerializer.Serialize(new {
                    path = path,
                    error = "Invalid magic - not a .uasset file"
                }));
                return;
            }

            // Get file size for basic stats
            var fileSize = fs.Length;

            // Detect asset type from filename (fast and reliable)
            var fileName = Path.GetFileNameWithoutExtension(resolvedPath);
            var assetType = DetectAssetTypeFromName(fileName);

            results.Add(JsonSerializer.Serialize(new {
                path = path,
                name = fileName,
                asset_type = assetType,
                size = fileSize
            }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new {
                path = path,
                error = ex.Message
            }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// Fast asset type detection from filename only (uses naming convention prefixes)
string DetectAssetTypeFromName(string fileName)
{
    var matchedType = "Unknown";
    var longestPrefix = 0;
    foreach (var kvp in NamingPrefixes)
    {
        if (fileName.StartsWith(kvp.Key, StringComparison.OrdinalIgnoreCase) && kvp.Key.Length > longestPrefix)
        {
            longestPrefix = kvp.Key.Length;
            matchedType = kvp.Value;
        }
    }
    return matchedType;
}

void BatchSummary(List<string> paths, EngineVersion engineVersion)
{
    // Output JSONL - one JSON object per line for easy parsing
    foreach (var path in paths)
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                Console.WriteLine(JsonSerializer.Serialize(new {
                    path = path,
                    error = "File not found"
                }));
                continue;
            }

            var asset = new UAsset(resolvedPath, engineVersion);

            var exportClasses = asset.Exports
                .Select(e => e.GetExportClassType()?.ToString() ?? "Unknown")
                .Distinct()
                .ToList();

            string assetType = DetectAssetType(resolvedPath, exportClasses);

            var mainExport = asset.Exports.FirstOrDefault();
            var mainExportName = mainExport?.ObjectName.ToString() ?? "Unknown";
            var mainExportClass = mainExport?.GetExportClassType()?.ToString() ?? "Unknown";

            Console.WriteLine(JsonSerializer.Serialize(new {
                path = path,
                asset_type = assetType,
                exports_count = asset.Exports.Count,
                imports_count = asset.Imports.Count,
                main_export = mainExportName,
                main_class = mainExportClass
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            Console.WriteLine(JsonSerializer.Serialize(new {
                path = path,
                error = "File locked",
                hint = "Close the asset in UE Editor"
            }));
        }
        catch (Exception ex)
        {
            Console.WriteLine(JsonSerializer.Serialize(new {
                path = path,
                error = ex.Message
            }));
        }
    }
}

void BatchReferences(List<string> paths, EngineVersion engineVersion)
{
    // Parallel processing with capped concurrency to avoid disk thrash
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new {
                    path = path,
                    error = "File not found"
                }));
                return;
            }

            var asset = new UAsset(resolvedPath, engineVersion);
            var exportClasses = asset.Exports
                .Select(e => e.GetExportClassType()?.ToString() ?? "Unknown")
                .Distinct()
                .ToList();
            var assetType = DetectAssetType(resolvedPath, exportClasses);
            var assetName = Path.GetFileNameWithoutExtension(resolvedPath);

            // Collect references (same logic as ExtractReferences but returning JSON)
            var assetRefs = new HashSet<string>();

            foreach (var import in asset.Imports)
            {
                var objectName = import.ObjectName.ToString();
                var className = import.ClassName.ToString();

                if (objectName.StartsWith("Default__")) continue;
                if (className == "Package" && !objectName.Contains("/Game/")) continue;

                string fullPath = "";

                if (className == "Package" && objectName.Contains("/Game/"))
                {
                    fullPath = objectName;
                }
                else
                {
                    var outerName = import.OuterIndex.Index == 0 ? "" :
                        (import.OuterIndex.IsImport() ? asset.Imports[-import.OuterIndex.Index - 1].ObjectName.ToString() : "");

                    if (!string.IsNullOrEmpty(outerName) && outerName.Contains("/Game/"))
                    {
                        fullPath = outerName;
                    }
                    else
                    {
                        // Walk the outer chain
                        var currentIdx = import.OuterIndex;
                        while (currentIdx.Index != 0)
                        {
                            if (currentIdx.IsImport())
                            {
                                var outerImport = asset.Imports[-currentIdx.Index - 1];
                                if (outerImport.ClassName.ToString() == "Package")
                                {
                                    var pkgName = outerImport.ObjectName.ToString();
                                    if (pkgName.Contains("/Game/"))
                                    {
                                        fullPath = pkgName;
                                        break;
                                    }
                                }
                                currentIdx = outerImport.OuterIndex;
                            }
                            else
                            {
                                break;
                            }
                        }
                    }
                }

                if (!string.IsNullOrEmpty(fullPath) && fullPath.StartsWith("/Game/"))
                {
                    assetRefs.Add(fullPath);
                }
            }

            // Also scan exports for object properties
            foreach (var export in asset.Exports)
            {
                if (export is NormalExport normalExport && normalExport.Data != null)
                {
                    foreach (var prop in normalExport.Data)
                    {
                        CollectAssetRefsFromProperty(asset, prop, assetRefs);
                    }
                }
            }

            results.Add(JsonSerializer.Serialize(new {
                path = path,
                name = assetName,
                asset_type = assetType,
                refs = assetRefs.OrderBy(r => r).ToList()
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            results.Add(JsonSerializer.Serialize(new {
                path = path,
                error = "File locked"
            }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new {
                path = path,
                error = ex.Message
            }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// ============================================================================
// BATCH BLUEPRINT - Extract blueprint data for multiple assets as JSONL
// ============================================================================
void BatchBlueprint(List<string> paths, EngineVersion engineVersion)
{
    // Parallel processing with capped concurrency to avoid disk thrash
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "File not found" }));
                return;
            }

            var asset = new UAsset(resolvedPath, engineVersion);

            // Extract blueprint data (same logic as ExtractBlueprint but JSON output)
            var classExport = asset.Exports.OfType<ClassExport>().FirstOrDefault();
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);

            var bpName = bpExport?.ObjectName.ToString() ?? classExport?.ObjectName.ToString() ?? "Unknown";

            // Parent class
            var parentClass = "Unknown";
            if (classExport?.SuperStruct != null && classExport.SuperStruct.Index != 0)
            {
                parentClass = ResolvePackageIndex(asset, classExport.SuperStruct);
                if (parentClass.Contains("_C"))
                    parentClass = parentClass.Replace("_C", "");
            }
            if (parentClass == "Unknown" || parentClass == "[null]")
            {
                var bpClassName = bpName + "_C";
                foreach (var import in asset.Imports)
                {
                    var importName = import.ObjectName.ToString();
                    var importClass = import.ClassName?.ToString() ?? "";
                    if (importName == bpClassName) continue;
                    if ((importClass == "Class" || importClass == "BlueprintGeneratedClass") && importName.EndsWith("_C"))
                    {
                        parentClass = importName.Replace("_C", "");
                        break;
                    }
                }
            }

            // Interfaces
            var interfaces = new List<string>();
            if (classExport?.Interfaces != null)
            {
                foreach (var iface in classExport.Interfaces)
                {
                    try
                    {
                        var classField = iface.GetType().GetField("Class", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                        if (classField != null)
                        {
                            var classValue = classField.GetValue(iface);
                            string ifaceName = "";
                            if (classValue is FPackageIndex pkgIndex)
                                ifaceName = ResolvePackageIndex(asset, pkgIndex);
                            else if (classValue is int intIndex)
                                ifaceName = ResolvePackageIndex(asset, new FPackageIndex(intIndex));
                            if (!string.IsNullOrEmpty(ifaceName) && ifaceName != "[null]")
                                interfaces.Add(ifaceName.EndsWith("_C") ? ifaceName[..^2] : ifaceName);
                        }
                    }
                    catch { }
                }
            }

            // Components
            var components = new List<string>();
            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                var exportName = export.ObjectName.ToString();
                if (exportName.StartsWith("Default__")) continue;
                if (className.Contains("Function") || className.Contains("BlueprintGeneratedClass")) continue;
                if (className.Contains("Blueprint") && !className.Contains("Component")) continue;
                if (className.Contains("Component"))
                {
                    var cleanName = exportName.Replace("_GEN_VARIABLE", "");
                    if (components.Count < 20)
                        components.Add(cleanName);
                }
            }

            // Events and Functions
            var events = new List<string>();
            var functions = new List<object>();
            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var funcName = funcExport.ObjectName.ToString();
                var flags = funcExport.FunctionFlags.ToString();

                if (funcName.StartsWith("ExecuteUbergraph") || funcName.StartsWith("bpv__") ||
                    funcName.StartsWith("__") || funcName.StartsWith("InpActEvt_") ||
                    funcName.StartsWith("InpAxisEvt_") || funcName.StartsWith("K2Node_") ||
                    funcName.Contains("__TRASHFUNC") || funcName.Contains("__TRASHEVENT"))
                    continue;

                bool isEvent = funcName.StartsWith("Receive") || funcName.StartsWith("OnRep_") ||
                              (flags.Contains("BlueprintEvent") && !flags.Contains("BlueprintCallable"));

                if (isEvent)
                {
                    if (events.Count < 15)
                        events.Add(funcName);
                }
                else
                {
                    var simpleFlags = new List<string>();
                    if (flags.Contains("BlueprintCallable")) simpleFlags.Add("Callable");
                    if (flags.Contains("BlueprintPure")) simpleFlags.Add("Pure");
                    if (flags.Contains("BlueprintEvent")) simpleFlags.Add("Event");

                    // Extract calls from bytecode
                    var funcCalls = new List<string>();
                    object controlFlow = null;
                    if (funcExport.ScriptBytecode != null)
                    {
                        var calls = new HashSet<string>();
                        var vars = new HashSet<string>();
                        var casts = new HashSet<string>();
                        foreach (var expr in funcExport.ScriptBytecode)
                            AnalyzeExpression(asset, expr, calls, vars, casts);
                        funcCalls = calls.Where(c => !string.IsNullOrEmpty(c) && c != "[null]" && !c.StartsWith("["))
                            .Where(c => !c.StartsWith("K2Node_") && !c.Contains("__"))
                            .Take(10).ToList();

                        // Analyze control flow for branching/complexity info
                        controlFlow = AnalyzeControlFlow(funcExport.ScriptBytecode);
                    }

                    if (functions.Count < 25)
                        functions.Add(new { name = funcName, flags = string.Join(",", simpleFlags), calls = funcCalls, control_flow = controlFlow });
                }
            }

            // Variables
            var variables = new List<string>();
            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!className.EndsWith("Property")) continue;
                var propName = export.ObjectName.ToString();
                if (propName.StartsWith("bpv__") || propName.StartsWith("K2Node_") ||
                    propName.StartsWith("Uber") || propName == "None") continue;
                var outer = export.OuterIndex.Index;
                if (outer > 0 && outer <= asset.Exports.Count)
                {
                    var outerClass = asset.Exports[outer - 1].GetExportClassType()?.ToString() ?? "";
                    if (outerClass.Contains("Function")) continue;
                }
                if (variables.Count < 30)
                    variables.Add(propName);
            }

            // Collect refs (include in output to avoid separate call)
            var refs = CollectAssetRefs(asset);

            results.Add(JsonSerializer.Serialize(new {
                path,
                name = bpName,
                parent = parentClass,
                interfaces,
                components,
                events,
                functions,
                variables,
                refs
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            results.Add(JsonSerializer.Serialize(new { path, error = "File locked" }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new { path, error = ex.Message }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// ============================================================================
// BATCH WIDGET - Extract widget data for multiple assets as JSONL
// ============================================================================
void BatchWidget(List<string> paths, EngineVersion engineVersion)
{
    // Parallel processing with capped concurrency to avoid disk thrash
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "File not found" }));
                return;
            }

            var asset = new UAsset(resolvedPath, engineVersion);

            // Extract blueprint metadata (parent class, interfaces, events, variables)
            var classExport = asset.Exports.OfType<ClassExport>().FirstOrDefault();
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);

            var bpName = bpExport?.ObjectName.ToString() ?? classExport?.ObjectName.ToString() ?? "Unknown";

            // Parent class - try multiple strategies
            var parentClass = "Unknown";

            // Strategy 1: ClassExport.SuperStruct (works when widget has BP logic)
            if (classExport?.SuperStruct != null && classExport.SuperStruct.Index != 0)
            {
                parentClass = ResolvePackageIndex(asset, classExport.SuperStruct);
                if (parentClass.Contains("_C"))
                    parentClass = parentClass.Replace("_C", "");
            }

            // Strategy 2: Check ParentClass property on ANY NormalExport
            if (parentClass == "Unknown" || parentClass == "[null]")
            {
                foreach (var export in asset.Exports.OfType<NormalExport>())
                {
                    if (export.Data == null) continue;
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "ParentClass" || propName == "NativeParentClass")
                        {
                            if (prop is ObjectPropertyData objProp && objProp.Value.Index != 0)
                            {
                                var resolved = ResolvePackageIndex(asset, objProp.Value);
                                if (!string.IsNullOrEmpty(resolved) && resolved != "[null]")
                                {
                                    parentClass = resolved.Replace("_C", "");
                                    break;
                                }
                            }
                        }
                    }
                    if (parentClass != "Unknown" && parentClass != "[null]") break;
                }
            }

            // Strategy 3: Look for parent class in imports - be more inclusive
            if (parentClass == "Unknown" || parentClass == "[null]")
            {
                var bpClassName = bpName + "_C";
                string bestCandidate = null;

                foreach (var import in asset.Imports)
                {
                    var importName = import.ObjectName.ToString();
                    var importClass = import.ClassName?.ToString() ?? "";
                    if (importName == bpClassName) continue;

                    // BlueprintGeneratedClass imports are parent widget classes
                    if (importClass == "BlueprintGeneratedClass" && importName.EndsWith("_C"))
                    {
                        var baseName = importName[..^2];
                        if (baseName.Contains("HUD") || baseName.Contains("Layout") ||
                            baseName.Contains("Activatable"))
                        {
                            parentClass = baseName;
                            break;
                        }
                        if (bestCandidate == null) bestCandidate = baseName;
                    }
                    else if (importClass == "Class" && importName.EndsWith("_C"))
                    {
                        var baseName = importName[..^2];
                        if (baseName.Contains("Widget") || baseName.Contains("UserWidget") ||
                            baseName.Contains("HUD") || baseName.Contains("Layout") ||
                            baseName.Contains("Activatable"))
                        {
                            if (bestCandidate == null) bestCandidate = baseName;
                        }
                    }
                }

                if ((parentClass == "Unknown" || parentClass == "[null]") && bestCandidate != null)
                    parentClass = bestCandidate;
            }

            // Strategy 4: For pure layout widgets, find parent by excluding engine widget classes
            if (parentClass == "Unknown" || parentClass == "[null]")
            {
                var bpClassName = bpName + "_C";

                foreach (var import in asset.Imports)
                {
                    var importName = import.ObjectName.ToString();
                    var importClass = import.ClassName?.ToString() ?? "";

                    if (importName == bpClassName) continue;

                    if (importClass == "BlueprintGeneratedClass" && importName.EndsWith("_C"))
                    {
                        // Check if outer is project/plugin code, not engine
                        var outerIdx = import.OuterIndex.Index;
                        if (outerIdx < 0)  // Negative index = import reference
                        {
                            var outer = asset.Imports[-outerIdx - 1];
                            var outerName = outer.ObjectName.ToString();
                            // Skip core engine packages
                            if (outerName.StartsWith("/Script/UMG") ||
                                outerName.StartsWith("/Script/Slate") ||
                                outerName == "/Script/Engine" ||
                                outerName == "/Script/CoreUObject")
                                continue;

                            // This is likely the parent widget class from project/plugin
                            parentClass = importName[..^2];
                            break;
                        }
                    }
                }
            }

            // Interfaces
            var interfaces = new List<string>();
            if (classExport?.Interfaces != null)
            {
                foreach (var iface in classExport.Interfaces)
                {
                    try
                    {
                        var classField = iface.GetType().GetField("Class", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                        if (classField != null)
                        {
                            var classValue = classField.GetValue(iface);
                            string ifaceName = null;
                            if (classValue is FPackageIndex pkgIndex)
                                ifaceName = ResolvePackageIndex(asset, pkgIndex);
                            else if (classValue is int intIndex)
                                ifaceName = ResolvePackageIndex(asset, new FPackageIndex(intIndex));
                            if (!string.IsNullOrEmpty(ifaceName) && ifaceName != "[null]")
                                interfaces.Add(ifaceName.EndsWith("_C") ? ifaceName[..^2] : ifaceName);
                        }
                    }
                    catch { }
                }
            }

            // Events and Functions
            var events = new List<string>();
            var functions = new List<object>();
            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var funcName = funcExport.ObjectName.ToString();
                var flags = funcExport.FunctionFlags.ToString();
                if (funcName.StartsWith("ExecuteUbergraph") || funcName.StartsWith("bpv__") ||
                    funcName.StartsWith("__") || funcName.StartsWith("InpActEvt_") ||
                    funcName.StartsWith("InpAxisEvt_") || funcName.StartsWith("K2Node_") ||
                    funcName.Contains("__TRASHFUNC")) continue;

                bool isEvent = funcName.StartsWith("Receive") || funcName.StartsWith("OnRep_") ||
                              (flags.Contains("BlueprintEvent") && !flags.Contains("BlueprintCallable"));

                if (isEvent)
                    events.Add(funcName);
                else
                {
                    var simpleFlags = new List<string>();
                    if (flags.Contains("BlueprintCallable")) simpleFlags.Add("Callable");
                    if (flags.Contains("BlueprintPure")) simpleFlags.Add("Pure");
                    if (flags.Contains("BlueprintEvent")) simpleFlags.Add("Event");
                    functions.Add(new { name = funcName, flags = string.Join(",", simpleFlags) });
                }
            }

            // Variables
            var variables = new List<string>();
            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (className.EndsWith("Property"))
                {
                    var propName = export.ObjectName.ToString();
                    if (propName.StartsWith("bpv__") || propName.StartsWith("K2Node_") ||
                        propName.StartsWith("Uber") || propName == "None") continue;

                    var outer = export.OuterIndex.Index;
                    if (outer > 0 && outer <= asset.Exports.Count)
                    {
                        var outerClass = asset.Exports[outer - 1].GetExportClassType()?.ToString() ?? "";
                        if (outerClass.Contains("Function")) continue;
                    }
                    if (!variables.Contains(propName))
                        variables.Add(propName);
                }
            }

            // Find WidgetTree and build slot->parent mapping
            int widgetTreeIndex = 0;
            var parentFromSlot = new Dictionary<int, int>();

            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i];
                var className = export.GetExportClassType()?.ToString() ?? "";
                var exportName = export.ObjectName.ToString();

                if (exportName == "WidgetTree" || className == "WidgetTree")
                    widgetTreeIndex = i + 1;

                if (className.Contains("Slot") && export is NormalExport slotExport && slotExport.Data != null)
                {
                    var slotIndex = i + 1;
                    foreach (var prop in slotExport.Data)
                    {
                        if (prop.Name.ToString() == "Parent" && prop is ObjectPropertyData parentProp && parentProp.Value.Index > 0)
                            parentFromSlot[slotIndex] = parentProp.Value.Index;
                    }
                }
            }

            // Collect widgets
            var widgets = new List<object>();
            var widgetNames = new List<string>();

            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i];
                var className = export.GetExportClassType()?.ToString() ?? "";
                var exportName = export.ObjectName.ToString();

                if (!IsWidgetClass(className)) continue;
                if (className.Contains("Slot") || exportName == "WidgetTree" || className == "WidgetTree") continue;
                if (className.Contains("GeneratedClass") || className == "WidgetBlueprint") continue;

                // Check if under WidgetTree
                bool isUnderWidgetTree = false;
                var currentOuter = export.OuterIndex.Index;
                while (currentOuter > 0 && currentOuter <= asset.Exports.Count)
                {
                    if (currentOuter == widgetTreeIndex) { isUnderWidgetTree = true; break; }
                    currentOuter = asset.Exports[currentOuter - 1].OuterIndex.Index;
                }
                if (!isUnderWidgetTree) continue;

                widgetNames.Add(exportName);

                // Extract text content if present
                string textContent = null;
                if (export is NormalExport normalExport && normalExport.Data != null)
                {
                    foreach (var prop in normalExport.Data)
                    {
                        if (prop.Name.ToString() == "Text" && prop is TextPropertyData textProp)
                        {
                            var textVal = GetTextPropertyValue(textProp);
                            if (!string.IsNullOrEmpty(textVal))
                                textContent = textVal;
                        }
                    }
                }

                var simpleType = className.Replace("CommonUI", "").Replace("User", "");
                widgets.Add(new { name = exportName, type = simpleType, text = textContent });
            }

            // Collect refs
            var refs = CollectAssetRefs(asset);

            // Build output with blueprint metadata
            var parent = (parentClass != "Unknown" && parentClass != "[null]") ? parentClass : null;

            results.Add(JsonSerializer.Serialize(new {
                path,
                parent,
                interfaces = interfaces.Count > 0 ? interfaces : null,
                events = events.Count > 0 ? events : null,
                functions = functions.Count > 0 ? functions : null,
                variables = variables.Count > 0 ? variables : null,
                widget_count = widgets.Count,
                widget_names = widgetNames,
                widgets,
                refs
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            results.Add(JsonSerializer.Serialize(new { path, error = "File locked" }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new { path, error = ex.Message }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// ============================================================================
// BATCH MATERIAL - Extract material data for multiple assets as JSONL
// ============================================================================
void BatchMaterial(List<string> paths, EngineVersion engineVersion)
{
    // Parallel processing with capped concurrency to avoid disk thrash
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "File not found" }));
                return;
            }

            var asset = new UAsset(resolvedPath, engineVersion);
            currentAsset = asset;

            // Find material export
            var materialExportBase = asset.Exports.FirstOrDefault(e =>
            {
                var cn = e.GetExportClassType()?.ToString() ?? "";
                return (cn == "Material" || cn.StartsWith("MaterialInstance") || cn == "MaterialFunction") &&
                       !cn.Contains("Expression");
            });

            if (materialExportBase == null)
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "No Material found" }));
                return;
            }

            var className = materialExportBase.GetExportClassType()?.ToString() ?? "";
            var isInstance = className.Contains("Instance");
            var matName = materialExportBase.ObjectName.ToString();
            var materialExport = materialExportBase as NormalExport;

            var scalarParams = new Dictionary<string, object>();
            var vectorParams = new Dictionary<string, object>();
            var textureParams = new Dictionary<string, string>();
            var staticSwitches = new Dictionary<string, bool>();
            string domain = "Surface", blendMode = "Opaque", shadingModel = "DefaultLit", parent = "";

            if (materialExport?.Data != null)
            {
                foreach (var prop in materialExport.Data)
                {
                    var propName = prop.Name.ToString();
                    if (propName == "MaterialDomain")
                        domain = GetPropertyValue(prop, 0)?.ToString() ?? "Surface";
                    else if (propName == "BlendMode")
                        blendMode = GetPropertyValue(prop, 0)?.ToString() ?? "Opaque";
                    else if (propName == "ShadingModel" || propName == "ShadingModels")
                        shadingModel = GetPropertyValue(prop, 0)?.ToString() ?? "DefaultLit";
                    else if (propName == "Parent" && prop is ObjectPropertyData parentProp)
                        parent = ResolvePackageIndex(asset, parentProp.Value);
                    else if (propName == "Parent")
                        parent = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    else if (propName == "ScalarParameterValues" && prop is ArrayPropertyData scalarArray)
                    {
                        foreach (var item in scalarArray.Value)
                        {
                            if (item is StructPropertyData structProp)
                            {
                                string pName = ""; object pValue = 0f;
                                foreach (var field in structProp.Value)
                                {
                                    var fn = field.Name.ToString();
                                    if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                    else if (fn == "ParameterValue") pValue = GetPropertyValue(field, 0) ?? 0f;
                                }
                                if (!string.IsNullOrEmpty(pName)) scalarParams[pName] = pValue;
                            }
                        }
                    }
                    else if (propName == "VectorParameterValues" && prop is ArrayPropertyData vectorArray)
                    {
                        foreach (var item in vectorArray.Value)
                        {
                            if (item is StructPropertyData structProp)
                            {
                                string pName = ""; var pValue = new List<object>();
                                foreach (var field in structProp.Value)
                                {
                                    var fn = field.Name.ToString();
                                    if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                    else if (fn == "ParameterValue" && field is StructPropertyData colorStruct)
                                    {
                                        foreach (var cf in colorStruct.Value)
                                            pValue.Add(GetPropertyValue(cf, 0) ?? 0f);
                                    }
                                }
                                if (!string.IsNullOrEmpty(pName)) vectorParams[pName] = pValue;
                            }
                        }
                    }
                    else if (propName == "TextureParameterValues" && prop is ArrayPropertyData textureArray)
                    {
                        foreach (var item in textureArray.Value)
                        {
                            if (item is StructPropertyData structProp)
                            {
                                string pName = "", pValue = "";
                                foreach (var field in structProp.Value)
                                {
                                    var fn = field.Name.ToString();
                                    if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                    else if (fn == "ParameterValue" && field is ObjectPropertyData texProp)
                                        pValue = ResolvePackageIndex(asset, texProp.Value);
                                }
                                if (!string.IsNullOrEmpty(pName)) textureParams[pName] = pValue;
                            }
                        }
                    }
                    else if ((propName == "StaticParametersRuntime" || propName == "StaticParameters") && prop is StructPropertyData staticStruct)
                    {
                        foreach (var field in staticStruct.Value)
                        {
                            if (field.Name.ToString() == "StaticSwitchParameters" && field is ArrayPropertyData switchArray)
                            {
                                foreach (var sw in switchArray.Value)
                                {
                                    if (sw is StructPropertyData swStruct)
                                    {
                                        string swName = ""; bool swValue = false;
                                        foreach (var sf in swStruct.Value)
                                        {
                                            var sfn = sf.Name.ToString();
                                            if (sfn == "ParameterInfo" && sf is StructPropertyData infoStruct)
                                            {
                                                foreach (var inf in infoStruct.Value)
                                                    if (inf.Name.ToString() == "Name")
                                                        swName = GetPropertyValue(inf, 0)?.ToString() ?? "";
                                            }
                                            else if (sfn == "Value")
                                                swValue = GetPropertyValue(sf, 0)?.ToString()?.ToLower() == "true";
                                        }
                                        if (!string.IsNullOrEmpty(swName)) staticSwitches[swName] = swValue;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Collect refs
            var refs = CollectAssetRefs(asset);

            results.Add(JsonSerializer.Serialize(new {
                path,
                name = matName,
                is_instance = isInstance,
                parent,
                domain,
                blend_mode = blendMode,
                shading_model = shadingModel,
                scalar_params = scalarParams,
                vector_params = vectorParams,
                texture_params = textureParams,
                static_switches = staticSwitches,
                refs
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            results.Add(JsonSerializer.Serialize(new { path, error = "File locked" }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new { path, error = ex.Message }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// ============================================================================
// BATCH DATATABLE - Extract datatable data for multiple assets as JSONL
// ============================================================================
void BatchDataTable(List<string> paths, EngineVersion engineVersion)
{
    // Parallel processing with capped concurrency to avoid disk thrash
    var options = new ParallelOptions { MaxDegreeOfParallelism = ResolveMaxParallelism(8) };
    var results = new System.Collections.Concurrent.ConcurrentBag<string>();

    Parallel.ForEach(paths, options, path =>
    {
        try
        {
            var resolvedPath = path;
            if (!File.Exists(resolvedPath) && File.Exists(resolvedPath + ".uasset"))
                resolvedPath = resolvedPath + ".uasset";

            if (!File.Exists(resolvedPath))
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "File not found" }));
                return;
            }

            var asset = new UAsset(resolvedPath, engineVersion);

            // Find DataTable export
            var dtExport = asset.Exports
                .OfType<DataTableExport>()
                .FirstOrDefault();

            if (dtExport == null)
            {
                results.Add(JsonSerializer.Serialize(new { path, error = "No DataTable found" }));
                return;
            }

            var tableName = dtExport.ObjectName.ToString();

            // Get row struct from first row's StructType (same as original ExtractDataTable)
            var rowStruct = dtExport.Table?.Data?.FirstOrDefault()?.StructType?.ToString() ?? "Unknown";
            var rowCount = dtExport.Table?.Data?.Count ?? 0;

            // Extract columns from first row and sample row keys
            var columns = new List<string>();
            var rowKeys = new List<string>();

            if (dtExport.Table?.Data != null && dtExport.Table.Data.Count > 0)
            {
                // Get columns from first row
                var firstRow = dtExport.Table.Data[0];
                if (firstRow.Value != null)
                {
                    foreach (var field in firstRow.Value)
                    {
                        var colName = field.Name.ToString();
                        var colType = field.PropertyType?.ToString() ?? "Unknown";
                        if (colName != "None")
                            columns.Add($"{colName}:{colType}");
                    }
                }

                // Get sample row keys
                foreach (var row in dtExport.Table.Data.Take(10))
                {
                    var rowKey = row.Name.ToString();
                    if (rowKey != "None")
                        rowKeys.Add(rowKey);
                }
            }

            // Collect refs
            var refs = CollectAssetRefs(asset);

            results.Add(JsonSerializer.Serialize(new {
                path,
                name = tableName,
                row_struct = rowStruct,
                row_count = rowCount,
                columns,
                sample_keys = rowKeys,
                refs
            }));
        }
        catch (IOException ex) when (ex.Message.Contains("being used by another process"))
        {
            results.Add(JsonSerializer.Serialize(new { path, error = "File locked" }));
        }
        catch (Exception ex)
        {
            results.Add(JsonSerializer.Serialize(new { path, error = ex.Message }));
        }
    });

    // Output all results
    foreach (var result in results)
    {
        Console.WriteLine(result);
    }
}

// ============================================================================
// HELPER: Collect asset references (shared by batch commands)
// ============================================================================
List<string> CollectAssetRefs(UAsset asset)
{
    var assetRefs = new HashSet<string>();

    // From imports
    foreach (var import in asset.Imports)
    {
        var objectName = import.ObjectName.ToString();
        var className = import.ClassName.ToString();

        if (objectName.StartsWith("Default__")) continue;
        if (className == "Package" && !objectName.Contains("/Game/")) continue;

        string fullPath = "";

        if (className == "Package" && objectName.Contains("/Game/"))
        {
            fullPath = objectName;
        }
        else
        {
            var currentIdx = import.OuterIndex;
            while (currentIdx.Index != 0)
            {
                if (currentIdx.IsImport())
                {
                    var outerImport = asset.Imports[-currentIdx.Index - 1];
                    if (outerImport.ClassName.ToString() == "Package")
                    {
                        var pkgName = outerImport.ObjectName.ToString();
                        if (pkgName.Contains("/Game/"))
                        {
                            fullPath = pkgName;
                            break;
                        }
                    }
                    currentIdx = outerImport.OuterIndex;
                }
                else break;
            }
        }

        if (!string.IsNullOrEmpty(fullPath) && fullPath.StartsWith("/Game/"))
            assetRefs.Add(fullPath);

        // Keep module-level script package refs (e.g., /Script/LyraGame)
        if (className == "Package" && objectName.StartsWith("/Script/", StringComparison.Ordinal))
            assetRefs.Add(objectName);

        // Keep likely class refs so semantic docs can link to gameplay systems.
        // Example: LyraHealthComponent -> /Script/LyraHealthComponent
        if (className == "Class" || className == "BlueprintGeneratedClass" || className == "WidgetBlueprintGeneratedClass")
        {
            var classRef = objectName;
            if (classRef.EndsWith("_C", StringComparison.Ordinal))
                classRef = classRef[..^2];
            if (IsLikelyClassRefName(classRef))
                assetRefs.Add("/Script/" + classRef);
        }
    }

    // From exports
    foreach (var export in asset.Exports)
    {
        if (export is NormalExport normalExport && normalExport.Data != null)
        {
            foreach (var prop in normalExport.Data)
                CollectAssetRefsFromProperty(asset, prop, assetRefs);
        }
    }

    return assetRefs.OrderBy(r => r).ToList();
}

bool IsLikelyClassRefName(string name)
{
    if (string.IsNullOrWhiteSpace(name)) return false;
    if (name == "None" || name == "[null]") return false;
    if (name.StartsWith("Default__", StringComparison.Ordinal)) return false;
    if (name.StartsWith("SKEL_", StringComparison.Ordinal) || name.StartsWith("REINST_", StringComparison.Ordinal)) return false;
    if (name.StartsWith("K2Node_", StringComparison.Ordinal) || name.StartsWith("EdGraph", StringComparison.Ordinal)) return false;
    return true;
}

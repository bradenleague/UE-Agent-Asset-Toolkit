using System.Reflection;
using System.Text.Json;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;
using UAssetAPI.UnrealTypes;
using UAssetAPI.CustomVersions;

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

        ProgramContext.args = args;

if (ProgramContext.args.Length < 2)
{
    Console.WriteLine("Usage: AssetParser.exe <command> <asset_path> [--version UE5_3]");
    Console.WriteLine();
    Console.WriteLine("Commands:");
    Console.WriteLine("  summary <path>    - Quick asset type detection and overview");
    Console.WriteLine("  inspect <path>    - Dump all exports and properties");
    Console.WriteLine("  widgets <path>    - Extract widget tree from Widget Blueprint");
    Console.WriteLine("  datatable <path>  - Extract rows from a DataTable");
    Console.WriteLine("  blueprint <path>  - Extract Blueprint functions and variables");
    Console.WriteLine("  graph <path>      - Extract Blueprint node graph as XML");
    Console.WriteLine("  graph-json <path> - Extract Blueprint node graph as JSON");
    Console.WriteLine("  bytecode <path>   - Extract bytecode control flow graph and pseudocode");
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
string assetPath = ProgramContext.assetPath = args[1];
EngineVersion engineVersion = ProgramContext.engineVersion = EngineVersion.VER_UE5_7;

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
        ProgramContext.currentAsset = null;

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
    currentAsset = ProgramContext.currentAsset = asset;

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
        case "graph":
            ExtractGraph(asset, "xml");
            break;
        case "graph-json":
            ExtractGraph(asset, "json");
            break;
        case "bytecode":
            ExtractBytecode(asset);
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


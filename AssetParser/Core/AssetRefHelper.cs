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
    public static class AssetRefHelper
    {
        public static List<string> CollectAssetRefs(UAsset asset)
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
        
        public static bool IsLikelyClassRefName(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return false;
            if (name == "None" || name == "[null]") return false;
            if (name.StartsWith("Default__", StringComparison.Ordinal)) return false;
            if (name.StartsWith("SKEL_", StringComparison.Ordinal) || name.StartsWith("REINST_", StringComparison.Ordinal)) return false;
            if (name.StartsWith("K2Node_", StringComparison.Ordinal) || name.StartsWith("EdGraph", StringComparison.Ordinal)) return false;
            return true;
        }
        
        
    }

public struct ParsedPin
{
    public string Name;
    public Guid PinId;
    public string Direction; // "in" or "out"
    public string Category;
    public string SubCategory;
    public string SubCategoryObject;
    public byte ContainerType;
    public string DefaultValue;
    public string AutoDefault;
    public bool IsHidden;
    public bool IsOrphaned;
    public List<(int nodeExportIndex, Guid pinGuid)> LinkedTo;
}

public class GraphPinData
{
    public string Name { get; set; } = "";
    public string Dir { get; set; } = "";
    public string Cat { get; set; } = "";
    public string? Sub { get; set; }
    public string? Container { get; set; }
    public string? Default { get; set; }
    public List<string>? To { get; set; }
}

public class GraphNodeData
{
    public int Id { get; set; }
    public string Type { get; set; } = "";
    public string? Target { get; set; }
    public List<GraphPinData> Pins { get; set; } = new();
}

public class GraphFunctionData
{
    public string Name { get; set; } = "";
    public List<GraphNodeData> Nodes { get; set; } = new();
}

public class GraphData
{
    public string Name { get; set; } = "";
    public List<GraphFunctionData> Functions { get; set; } = new();
    public List<object>? Errors { get; set; }
}

// Bytecode CFG types
public class CFGBlock
{
    public int Id;
    public uint StartOffset;
    public List<int> Instructions = new();  // Indices into ScriptBytecode[]
    public List<int> Successors = new();    // Block IDs of successor blocks
    public bool IsLoopTarget;               // True if targeted by a back-edge
}

public class CFGResult
{
    public List<CFGBlock> Blocks = new();
    public Dictionary<uint, int> OffsetToBlock = new();  // Byte offset → block ID
}

}

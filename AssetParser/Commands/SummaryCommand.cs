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

namespace AssetParser.Commands
{
    public static class SummaryCommand
    {
        public static void SummarizeAsset(UAsset asset)
        {
            var result = new Dictionary<string, object>
            {
                ["path"] = ProgramContext.assetPath,
                ["engine_version"] = ProgramContext.engineVersion.ToString(),
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
            string assetType = DetectAssetType(ProgramContext.assetPath, exportClasses);
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
        
        public static string DetectAssetType(string path, List<string> exportClasses)
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
        
        
    }
}

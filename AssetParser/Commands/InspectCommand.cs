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
    public static class InspectCommand
    {
        public static void InspectAsset(UAsset asset)
        {
            var result = new Dictionary<string, object>
            {
                ["path"] = ProgramContext.assetPath,
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
        
        
    }
}

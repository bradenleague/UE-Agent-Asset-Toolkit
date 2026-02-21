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
    public static class BatchDataTableCommand
    {
        public static void BatchDataTable(List<string> paths, EngineVersion engineVersion)
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
        
                    var asset = new UAsset(resolvedPath, ProgramContext.engineVersion);
        
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
        
        
    }
}

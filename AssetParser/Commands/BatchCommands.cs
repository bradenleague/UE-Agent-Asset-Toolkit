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
    public static class BatchCommands
    {
        
        public static int ResolveMaxParallelism(int fallback)
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
        public static void BatchFastSummary(List<string> paths)
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
        public static string DetectAssetTypeFromName(string fileName)
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
        
        public static void BatchSummary(List<string> paths, EngineVersion engineVersion)
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
        
                    var asset = new UAsset(resolvedPath, ProgramContext.engineVersion);
        
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
        
        public static void BatchReferences(List<string> paths, EngineVersion engineVersion)
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
        
                    var asset = new UAsset(resolvedPath, ProgramContext.engineVersion);
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
        
        
    }
}

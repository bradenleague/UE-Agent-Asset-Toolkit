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
    public static class BatchBlueprintCommand
    {
        public static void BatchBlueprint(List<string> paths, EngineVersion engineVersion)
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
        
                            // Extract parameters (same logic as ExtractBlueprint)
                            var parameters = new List<object>();
                            if (funcExport.LoadedProperties != null && funcExport.LoadedProperties.Length > 0)
                            {
                                foreach (var prop in funcExport.LoadedProperties)
                                {
                                    if (!prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_Parm)) continue;
                                    var paramName = prop.Name?.ToString() ?? "Unknown";
                                    var paramType = (prop.SerializedType?.ToString() ?? "Unknown").Replace("Property", "");
                                    string direction = prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReturnParm) ? "return"
                                        : (prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_OutParm) && !prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReferenceParm)) ? "out" : "in";
                                    parameters.Add(new { name = paramName, type = paramType, direction });
                                }
                            }
                            else
                            {
                                var funcIndex = Array.IndexOf(asset.Exports.ToArray(), funcExport) + 1;
                                foreach (var export in asset.Exports)
                                {
                                    var cn = export.GetExportClassType()?.ToString() ?? "";
                                    if (!cn.EndsWith("Property") || export.OuterIndex.Index != funcIndex) continue;
                                    parameters.Add(new { name = export.ObjectName.ToString(), type = cn.Replace("Property", ""), direction = "in" });
                                }
                            }
        
                            if (functions.Count < 25)
                                functions.Add(new { name = funcName, flags = string.Join(",", simpleFlags), calls = funcCalls, control_flow = controlFlow, @params = parameters.Count > 0 ? parameters : null });
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
        
        
    }
}

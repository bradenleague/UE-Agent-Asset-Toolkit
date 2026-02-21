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
    public static class BatchWidgetCommand
    {
        public static void BatchWidget(List<string> paths, EngineVersion engineVersion)
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
        
        
    }
}

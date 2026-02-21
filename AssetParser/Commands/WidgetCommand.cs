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
    public static class WidgetCommand
    {
        public static void ExtractWidgets(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
            xml.AppendLine("<widget-blueprint>");
        
            // Extract blueprint metadata (parent class, interfaces, events, variables)
            var classExport = asset.Exports.OfType<ClassExport>().FirstOrDefault();
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);
        
            // Get asset name from filename if no blueprint export
            var bpName = bpExport?.ObjectName.ToString() ?? classExport?.ObjectName.ToString() ?? Path.GetFileNameWithoutExtension(ProgramContext.args[1]);
        
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
        
        public static bool IsWidgetClass(string className)
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
        
        public static void WriteWidgetXml(System.Text.StringBuilder xml, int widgetIndex,
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
        
        
    }
}

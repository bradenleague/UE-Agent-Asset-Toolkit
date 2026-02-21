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
    public static class BlueprintCommand
    {
        public static void ExtractBlueprint(UAsset asset)
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
        
        public static string EscapeXml(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            return text
                .Replace("&", "&amp;")
                .Replace("<", "&lt;")
                .Replace(">", "&gt;")
                .Replace("\"", "&quot;")
                .Replace("'", "&apos;");
        }
        
        
    }
}

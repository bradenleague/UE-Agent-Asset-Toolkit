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
    public static class MaterialCommand
    {
        public static void ExtractMaterial(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
        
            // Find material exports - must be Material or MaterialInstance, not MaterialExpression
            // Check all export types (NormalExport, RawExport, etc.)
            var materialExportBase = asset.Exports
                .FirstOrDefault(e =>
                {
                    var cn = e.GetExportClassType()?.ToString() ?? "";
                    // Match Material, MaterialInstance, MaterialInstanceConstant, etc.
                    // But NOT MaterialExpression*, MaterialFunction*
                    return (cn == "Material" ||
                            cn.StartsWith("MaterialInstance") ||
                            cn == "MaterialFunction") &&
                           !cn.Contains("Expression");
                });
        
            if (materialExportBase == null)
            {
                xml.AppendLine("<material>");
                xml.AppendLine("  <error>No Material found in asset</error>");
                xml.AppendLine("</material>");
                Console.WriteLine(xml.ToString());
                return;
            }
        
            var className = materialExportBase.GetExportClassType()?.ToString() ?? "";
            var isInstance = className.Contains("Instance");
            var matName = materialExportBase.ObjectName.ToString();
        
            // Try to cast to NormalExport for property access (MaterialInstances)
            var materialExport = materialExportBase as NormalExport;
        
            // Collect parameters
            var scalarParams = new List<(string name, object value, string group)>();
            var vectorParams = new List<(string name, List<object> value, string group)>();
            var textureParams = new List<(string name, string texture, string group)>();
            var staticSwitches = new List<(string name, bool value)>();
        
            string domain = "Surface";
            string blendMode = "Opaque";
            string shadingModel = "DefaultLit";
            string parent = "";
        
            if (materialExport?.Data != null)
            {
                foreach (var prop in materialExport.Data)
                {
                    var propName = prop.Name.ToString();
        
                    if (propName == "MaterialDomain")
                        domain = GetPropertyValue(prop, 0)?.ToString() ?? "Surface";
                    else if (propName == "BlendMode")
                        blendMode = GetPropertyValue(prop, 0)?.ToString() ?? "Opaque";
                    else if (propName == "ShadingModel" || propName == "ShadingModels")
                        shadingModel = GetPropertyValue(prop, 0)?.ToString() ?? "DefaultLit";
                    else if (propName == "Parent" && prop is ObjectPropertyData parentProp)
                        parent = ResolvePackageIndex(asset, parentProp.Value);
                    else if (propName == "Parent")
                        parent = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    else if (propName == "ScalarParameterValues" && prop is ArrayPropertyData scalarArray)
                        ExtractScalarParametersXml(scalarArray, scalarParams);
                    else if (propName == "VectorParameterValues" && prop is ArrayPropertyData vectorArray)
                        ExtractVectorParametersXml(vectorArray, vectorParams);
                    else if (propName == "TextureParameterValues" && prop is ArrayPropertyData textureArray)
                        ExtractTextureParametersXml(asset, textureArray, textureParams);
                    else if ((propName == "StaticParametersRuntime" || propName == "StaticParameters") && prop is StructPropertyData staticStruct)
                        ExtractStaticSwitchesXml(staticStruct, staticSwitches);
                }
            }
        
            // Output XML
            xml.AppendLine(isInstance ? "<material-instance>" : "<material>");
        
            xml.AppendLine($"  <name>{EscapeXml(matName)}</name>");
        
            if (isInstance && !string.IsNullOrEmpty(parent) && parent != "[null]")
                xml.AppendLine($"  <parent>{EscapeXml(parent)}</parent>");
        
            xml.AppendLine($"  <domain>{EscapeXml(domain)}</domain>");
            xml.AppendLine($"  <blend-mode>{EscapeXml(blendMode)}</blend-mode>");
            xml.AppendLine($"  <shading-model>{EscapeXml(shadingModel)}</shading-model>");
        
            // Parameters section
            if (scalarParams.Count > 0 || vectorParams.Count > 0 || textureParams.Count > 0)
            {
                xml.AppendLine("  <parameters>");
        
                foreach (var (name, value, group) in scalarParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    xml.AppendLine($"    <scalar name=\"{EscapeXml(name)}\" value=\"{value}\"{groupAttr} />");
                }
        
                foreach (var (name, value, group) in vectorParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    var rgba = string.Join(",", value.Select(v => v?.ToString() ?? "0"));
                    xml.AppendLine($"    <vector name=\"{EscapeXml(name)}\" rgba=\"{rgba}\"{groupAttr} />");
                }
        
                foreach (var (name, texture, group) in textureParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    xml.AppendLine($"    <texture name=\"{EscapeXml(name)}\" ref=\"{EscapeXml(texture)}\"{groupAttr} />");
                }
        
                xml.AppendLine("  </parameters>");
            }
        
            // Static switches for instances
            if (staticSwitches.Count > 0)
            {
                xml.AppendLine("  <static-switches>");
                foreach (var (name, value) in staticSwitches)
                {
                    xml.AppendLine($"    <switch name=\"{EscapeXml(name)}\" value=\"{value.ToString().ToLower()}\" />");
                }
                xml.AppendLine("  </static-switches>");
            }
        
            // For base materials (not instances), extract expression nodes
            if (!isInstance)
            {
                var expressionNodes = new List<(string type, string name, string details)>();
                var texturesUsed = new HashSet<string>();
        
                foreach (var export in asset.Exports)
                {
                    var expClassName = export.GetExportClassType()?.ToString() ?? "";
                    if (!expClassName.StartsWith("MaterialExpression")) continue;
        
                    var expName = export.ObjectName.ToString();
                    var nodeType = expClassName.Replace("MaterialExpression", "");
        
                    // Extract parameter name and value for parameter nodes
                    if (export is NormalExport normalExp && normalExp.Data != null)
                    {
                        string paramName = "";
                        string paramValue = "";
                        string paramGroup = "";
        
                        foreach (var prop in normalExp.Data)
                        {
                            var pn = prop.Name.ToString();
                            if (pn == "ParameterName")
                                paramName = GetPropertyValue(prop, 0)?.ToString() ?? "";
                            else if (pn == "DefaultValue")
                            {
                                var val = GetPropertyValue(prop, 0);
                                // Use FormatValue for structs (like LinearColor/Vector)
                                paramValue = FormatValue(val);
                            }
                            else if (pn == "Group")
                                paramGroup = GetPropertyValue(prop, 0)?.ToString() ?? "";
                            else if (pn == "Texture" && prop is ObjectPropertyData texProp && texProp.Value.Index != 0)
                            {
                                var texName = ResolvePackageIndex(asset, texProp.Value);
                                if (!string.IsNullOrEmpty(texName) && texName != "[null]")
                                    texturesUsed.Add(texName);
                            }
                        }
        
                        var details = "";
                        if (!string.IsNullOrEmpty(paramName))
                        {
                            details = paramName;
                            if (!string.IsNullOrEmpty(paramValue))
                                details += $"={paramValue}";
                            if (!string.IsNullOrEmpty(paramGroup))
                                details += $" [{paramGroup}]";
                        }
        
                        expressionNodes.Add((nodeType, expName, details));
                    }
                    else
                    {
                        expressionNodes.Add((nodeType, expName, ""));
                    }
                }
        
                // Output expression nodes (group by type)
                if (expressionNodes.Count > 0)
                {
                    xml.AppendLine("  <expressions>");
        
                    // Parameters first
                    var paramNodes = expressionNodes.Where(n =>
                        n.type.Contains("Parameter") ||
                        n.type.Contains("TextureSample")).ToList();
        
                    foreach (var (type, name, details) in paramNodes)
                    {
                        var detailAttr = !string.IsNullOrEmpty(details) ? $" details=\"{EscapeXml(details)}\"" : "";
                        xml.AppendLine($"    <node type=\"{EscapeXml(type)}\"{detailAttr} />");
                    }
        
                    // Then count of other node types
                    var otherNodes = expressionNodes.Where(n =>
                        !n.type.Contains("Parameter") &&
                        !n.type.Contains("TextureSample")).ToList();
        
                    var nodeCounts = otherNodes.GroupBy(n => n.type)
                        .Select(g => (g.Key, g.Count()))
                        .OrderByDescending(x => x.Item2);
        
                    foreach (var (type, count) in nodeCounts)
                    {
                        xml.AppendLine($"    <node type=\"{EscapeXml(type)}\" count=\"{count}\" />");
                    }
        
                    xml.AppendLine("  </expressions>");
                }
        
                // Output textures used
                if (texturesUsed.Count > 0)
                {
                    xml.AppendLine("  <textures>");
                    foreach (var tex in texturesUsed.OrderBy(t => t))
                    {
                        xml.AppendLine($"    <texture ref=\"{EscapeXml(tex)}\" />");
                    }
                    xml.AppendLine("  </textures>");
                }
            }
        
            xml.AppendLine(isInstance ? "</material-instance>" : "</material>");
        
            Console.WriteLine(xml.ToString());
        }
        
        public static void ExtractScalarParametersXml(ArrayPropertyData arrayProp, List<(string name, object value, string group)> output)
        {
            if (arrayProp.Value == null) return;
        
            foreach (var item in arrayProp.Value)
            {
                if (item is StructPropertyData structProp && structProp.Value != null)
                {
                    string name = "";
                    object value = 0;
                    string group = "";
        
                    foreach (var field in structProp.Value)
                    {
                        var fieldName = field.Name.ToString();
                        if (fieldName == "ParameterName" || fieldName == "Name")
                            name = GetPropertyValue(field, 0)?.ToString() ?? "";
                        else if (fieldName == "ParameterValue" || fieldName == "Value")
                            value = GetPropertyValue(field, 0) ?? 0;
                        else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                        {
                            foreach (var infoField in infoStruct.Value)
                            {
                                if (infoField.Name.ToString() == "Name")
                                    name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                                else if (infoField.Name.ToString() == "Group")
                                    group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                            }
                        }
                    }
        
                    if (!string.IsNullOrEmpty(name))
                        output.Add((name, value, group));
                }
            }
        }
        
        public static void ExtractVectorParametersXml(ArrayPropertyData arrayProp, List<(string name, List<object> value, string group)> output)
        {
            if (arrayProp.Value == null) return;
        
            foreach (var item in arrayProp.Value)
            {
                if (item is StructPropertyData structProp && structProp.Value != null)
                {
                    string name = "";
                    var value = new List<object> { 0, 0, 0, 1 };
                    string group = "";
        
                    foreach (var field in structProp.Value)
                    {
                        var fieldName = field.Name.ToString();
                        if (fieldName == "ParameterName" || fieldName == "Name")
                            name = GetPropertyValue(field, 0)?.ToString() ?? "";
                        else if (fieldName == "ParameterValue" || fieldName == "Value")
                        {
                            var val = GetPropertyValue(field, 1);
                            if (val is Dictionary<string, object> colorDict)
                            {
                                var r = colorDict.GetValueOrDefault("R", colorDict.GetValueOrDefault("r", 0));
                                var g = colorDict.GetValueOrDefault("G", colorDict.GetValueOrDefault("g", 0));
                                var b = colorDict.GetValueOrDefault("B", colorDict.GetValueOrDefault("b", 0));
                                var a = colorDict.GetValueOrDefault("A", colorDict.GetValueOrDefault("a", 1));
                                value = new List<object> { r, g, b, a };
                            }
                        }
                        else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                        {
                            foreach (var infoField in infoStruct.Value)
                            {
                                if (infoField.Name.ToString() == "Name")
                                    name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                                else if (infoField.Name.ToString() == "Group")
                                    group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                            }
                        }
                    }
        
                    if (!string.IsNullOrEmpty(name))
                        output.Add((name, value, group));
                }
            }
        }
        
        public static void ExtractTextureParametersXml(UAsset asset, ArrayPropertyData arrayProp, List<(string name, string texture, string group)> output)
        {
            if (arrayProp.Value == null) return;
        
            foreach (var item in arrayProp.Value)
            {
                if (item is StructPropertyData structProp && structProp.Value != null)
                {
                    string name = "";
                    string texture = "";
                    string group = "";
        
                    foreach (var field in structProp.Value)
                    {
                        var fieldName = field.Name.ToString();
                        if (fieldName == "ParameterName" || fieldName == "Name")
                            name = GetPropertyValue(field, 0)?.ToString() ?? "";
                        else if (fieldName == "ParameterValue" || fieldName == "Value")
                        {
                            if (field is ObjectPropertyData objProp)
                                texture = ResolvePackageIndex(asset, objProp.Value);
                            else if (field is SoftObjectPropertyData softProp)
                                texture = softProp.Value.ToString();
                            else
                                texture = GetPropertyValue(field, 0)?.ToString() ?? "";
                        }
                        else if (fieldName == "ParameterInfo" && field is StructPropertyData infoStruct && infoStruct.Value != null)
                        {
                            foreach (var infoField in infoStruct.Value)
                            {
                                if (infoField.Name.ToString() == "Name")
                                    name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                                else if (infoField.Name.ToString() == "Group")
                                    group = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                            }
                        }
                    }
        
                    if (!string.IsNullOrEmpty(name))
                        output.Add((name, texture, group));
                }
            }
        }
        
        public static void ExtractStaticSwitchesXml(StructPropertyData staticStruct, List<(string name, bool value)> output)
        {
            if (staticStruct.Value == null) return;
        
            foreach (var field in staticStruct.Value)
            {
                var fieldName = field.Name.ToString();
                if (fieldName.Contains("Switch") && field is ArrayPropertyData switchArray && switchArray.Value != null)
                {
                    foreach (var sw in switchArray.Value)
                    {
                        if (sw is StructPropertyData swStruct && swStruct.Value != null)
                        {
                            string name = "";
                            bool value = false;
        
                            foreach (var swField in swStruct.Value)
                            {
                                var swFieldName = swField.Name.ToString();
                                if (swFieldName == "ParameterName" || swFieldName == "Name")
                                    name = GetPropertyValue(swField, 0)?.ToString() ?? "";
                                else if (swFieldName == "Value" || swFieldName == "ParameterValue")
                                    value = GetPropertyValue(swField, 0) as bool? ?? false;
                                else if (swFieldName == "ParameterInfo" && swField is StructPropertyData infoStruct && infoStruct.Value != null)
                                {
                                    foreach (var infoField in infoStruct.Value)
                                    {
                                        if (infoField.Name.ToString() == "Name")
                                            name = GetPropertyValue(infoField, 0)?.ToString() ?? "";
                                    }
                                }
                            }
        
                            if (!string.IsNullOrEmpty(name))
                                output.Add((name, value));
                        }
                    }
                }
            }
        }
        
        
    }
}

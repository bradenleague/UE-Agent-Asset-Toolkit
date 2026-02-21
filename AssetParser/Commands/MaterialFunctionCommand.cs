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
    public static class MaterialFunctionCommand
    {
        public static void ExtractMaterialFunction(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
        
            // Find the main MaterialFunction export
            var mfExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString() == "MaterialFunction");
        
            if (mfExport == null)
            {
                xml.AppendLine("<material-function>");
                xml.AppendLine("  <error>No MaterialFunction found in asset</error>");
                xml.AppendLine("</material-function>");
                Console.WriteLine(xml.ToString());
                return;
            }
        
            var mfName = mfExport.ObjectName.ToString();
        
            // Collect inputs from MaterialExpressionFunctionInput exports
            var inputs = new List<(string name, string type, int priority)>();
            foreach (var export in asset.Exports.OfType<NormalExport>())
            {
                if (export.GetExportClassType()?.ToString() == "MaterialExpressionFunctionInput" && export.Data != null)
                {
                    string inputName = "Input";
                    string inputType = "Vector3";  // Default type
                    int sortPriority = 0;
        
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "InputName")
                            inputName = GetPropertyValue(prop, 0)?.ToString() ?? "Input";
                        else if (propName == "InputType" && prop is BytePropertyData byteProp)
                        {
                            // Map InputType enum values to readable names
                            var typeVal = byteProp.Value;
                            inputType = typeVal switch
                            {
                                0 => "Scalar",
                                1 => "Vector2",
                                2 => "Vector3",
                                3 => "Vector4",
                                4 => "Texture2D",
                                5 => "TextureCube",
                                6 => "Texture2DArray",
                                7 => "VolumeTexture",
                                8 => "StaticBool",
                                9 => "MaterialAttributes",
                                10 => "External",
                                _ => $"Type{typeVal}"
                            };
                        }
                        else if (propName == "SortPriority")
                            sortPriority = (int)(GetPropertyValue(prop, 0) ?? 0);
                    }
                    inputs.Add((inputName, inputType, sortPriority));
                }
            }
        
            // Collect outputs from MaterialExpressionFunctionOutput exports
            var outputs = new List<(string name, int priority)>();
            foreach (var export in asset.Exports.OfType<NormalExport>())
            {
                if (export.GetExportClassType()?.ToString() == "MaterialExpressionFunctionOutput" && export.Data != null)
                {
                    string outputName = "Result";  // Default name
                    int sortPriority = 0;
        
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "OutputName")
                            outputName = GetPropertyValue(prop, 0)?.ToString() ?? "Result";
                        else if (propName == "SortPriority")
                            sortPriority = (int)(GetPropertyValue(prop, 0) ?? 0);
                    }
                    outputs.Add((outputName, sortPriority));
                }
            }
        
            // Collect scalar parameters
            var scalarParams = new List<(string name, float defaultVal, string group)>();
            foreach (var export in asset.Exports.OfType<NormalExport>())
            {
                if (export.GetExportClassType()?.ToString() == "MaterialExpressionScalarParameter" && export.Data != null)
                {
                    string paramName = "Parameter";
                    float defaultVal = 0f;
                    string group = "";
        
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "ParameterName")
                            paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                        else if (propName == "DefaultValue" && prop is FloatPropertyData floatProp)
                            defaultVal = floatProp.Value;
                        else if (propName == "Group")
                            group = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    }
                    scalarParams.Add((paramName, defaultVal, group));
                }
            }
        
            // Collect vector parameters
            var vectorParams = new List<(string name, string defaultVal, string group)>();
            foreach (var export in asset.Exports.OfType<NormalExport>())
            {
                if (export.GetExportClassType()?.ToString() == "MaterialExpressionVectorParameter" && export.Data != null)
                {
                    string paramName = "Parameter";
                    string defaultVal = "0,0,0,1";
                    string group = "";
        
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "ParameterName")
                            paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                        else if (propName == "DefaultValue" && prop is StructPropertyData)
                        {
                            // LinearColor struct - extract RGBA values
                            var colorData = GetPropertyValue(prop, 1);
                            if (colorData is Dictionary<string, object> colorDict)
                            {
                                // Check for R,G,B,A keys first (standard struct format)
                                if (colorDict.ContainsKey("R") || colorDict.ContainsKey("r"))
                                {
                                    var r = colorDict.GetValueOrDefault("R", colorDict.GetValueOrDefault("r", 0));
                                    var g = colorDict.GetValueOrDefault("G", colorDict.GetValueOrDefault("g", 0));
                                    var b = colorDict.GetValueOrDefault("B", colorDict.GetValueOrDefault("b", 0));
                                    var a = colorDict.GetValueOrDefault("A", colorDict.GetValueOrDefault("a", 1));
                                    defaultVal = $"{r},{g},{b},{a}";
                                }
                                // LinearColor stores value as a string "(R, G, B, A)" in DefaultValue property
                                else if (colorDict.TryGetValue("DefaultValue", out var colorStr) && colorStr is string cs)
                                {
                                    // Parse "(R, G, B, A)" format and reformat as "R,G,B,A"
                                    if (cs.StartsWith("(") && cs.EndsWith(")"))
                                    {
                                        defaultVal = cs.Trim('(', ')').Replace(" ", "");
                                    }
                                }
                            }
                        }
                        else if (propName == "Group")
                            group = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    }
                    vectorParams.Add((paramName, defaultVal, group));
                }
            }
        
            // Collect static switch parameters
            var switchParams = new List<(string name, bool defaultVal, string group)>();
            foreach (var export in asset.Exports.OfType<NormalExport>())
            {
                if (export.GetExportClassType()?.ToString() == "MaterialExpressionStaticSwitchParameter" && export.Data != null)
                {
                    string paramName = "Parameter";
                    bool defaultVal = false;
                    string group = "";
        
                    foreach (var prop in export.Data)
                    {
                        var propName = prop.Name.ToString();
                        if (propName == "ParameterName")
                            paramName = GetPropertyValue(prop, 0)?.ToString() ?? "Parameter";
                        else if (propName == "DefaultValue" && prop is BoolPropertyData boolProp)
                            defaultVal = boolProp.Value;
                        else if (propName == "Group")
                            group = GetPropertyValue(prop, 0)?.ToString() ?? "";
                    }
                    switchParams.Add((paramName, defaultVal, group));
                }
            }
        
            // Output XML
            xml.AppendLine("<material-function>");
            xml.AppendLine($"  <name>{EscapeXml(mfName)}</name>");
        
            // Inputs section (sorted by priority)
            if (inputs.Count > 0)
            {
                xml.AppendLine("  <inputs>");
                foreach (var (name, type, priority) in inputs.OrderBy(i => i.priority))
                {
                    xml.AppendLine($"    <input name=\"{EscapeXml(name)}\" type=\"{EscapeXml(type)}\" priority=\"{priority}\" />");
                }
                xml.AppendLine("  </inputs>");
            }
        
            // Outputs section (sorted by priority)
            if (outputs.Count > 0)
            {
                xml.AppendLine("  <outputs>");
                foreach (var (name, priority) in outputs.OrderBy(o => o.priority))
                {
                    xml.AppendLine($"    <output name=\"{EscapeXml(name)}\" priority=\"{priority}\" />");
                }
                xml.AppendLine("  </outputs>");
            }
        
            // Parameters section
            if (scalarParams.Count > 0 || vectorParams.Count > 0 || switchParams.Count > 0)
            {
                xml.AppendLine("  <parameters>");
        
                foreach (var (name, defaultVal, group) in scalarParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    xml.AppendLine($"    <scalar name=\"{EscapeXml(name)}\" default=\"{defaultVal}\"{groupAttr} />");
                }
        
                foreach (var (name, defaultVal, group) in vectorParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    xml.AppendLine($"    <vector name=\"{EscapeXml(name)}\" default=\"{defaultVal}\"{groupAttr} />");
                }
        
                foreach (var (name, defaultVal, group) in switchParams)
                {
                    var groupAttr = !string.IsNullOrEmpty(group) ? $" group=\"{EscapeXml(group)}\"" : "";
                    xml.AppendLine($"    <switch name=\"{EscapeXml(name)}\" default=\"{defaultVal.ToString().ToLower()}\"{groupAttr} />");
                }
        
                xml.AppendLine("  </parameters>");
            }
        
            xml.AppendLine("</material-function>");
        
            Console.WriteLine(xml.ToString());
        }
        
        
    }
}

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
    public static class BatchMaterialCommand
    {
        public static void BatchMaterial(List<string> paths, EngineVersion engineVersion)
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
                    ProgramContext.currentAsset = asset;
        
                    // Find material export
                    var materialExportBase = asset.Exports.FirstOrDefault(e =>
                    {
                        var cn = e.GetExportClassType()?.ToString() ?? "";
                        return (cn == "Material" || cn.StartsWith("MaterialInstance") || cn == "MaterialFunction") &&
                               !cn.Contains("Expression");
                    });
        
                    if (materialExportBase == null)
                    {
                        results.Add(JsonSerializer.Serialize(new { path, error = "No Material found" }));
                        return;
                    }
        
                    var className = materialExportBase.GetExportClassType()?.ToString() ?? "";
                    var isInstance = className.Contains("Instance");
                    var matName = materialExportBase.ObjectName.ToString();
                    var materialExport = materialExportBase as NormalExport;
        
                    var scalarParams = new Dictionary<string, object>();
                    var vectorParams = new Dictionary<string, object>();
                    var textureParams = new Dictionary<string, string>();
                    var staticSwitches = new Dictionary<string, bool>();
                    string domain = "Surface", blendMode = "Opaque", shadingModel = "DefaultLit", parent = "";
        
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
                            {
                                foreach (var item in scalarArray.Value)
                                {
                                    if (item is StructPropertyData structProp)
                                    {
                                        string pName = ""; object pValue = 0f;
                                        foreach (var field in structProp.Value)
                                        {
                                            var fn = field.Name.ToString();
                                            if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                            else if (fn == "ParameterValue") pValue = GetPropertyValue(field, 0) ?? 0f;
                                        }
                                        if (!string.IsNullOrEmpty(pName)) scalarParams[pName] = pValue;
                                    }
                                }
                            }
                            else if (propName == "VectorParameterValues" && prop is ArrayPropertyData vectorArray)
                            {
                                foreach (var item in vectorArray.Value)
                                {
                                    if (item is StructPropertyData structProp)
                                    {
                                        string pName = ""; var pValue = new List<object>();
                                        foreach (var field in structProp.Value)
                                        {
                                            var fn = field.Name.ToString();
                                            if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                            else if (fn == "ParameterValue" && field is StructPropertyData colorStruct)
                                            {
                                                foreach (var cf in colorStruct.Value)
                                                    pValue.Add(GetPropertyValue(cf, 0) ?? 0f);
                                            }
                                        }
                                        if (!string.IsNullOrEmpty(pName)) vectorParams[pName] = pValue;
                                    }
                                }
                            }
                            else if (propName == "TextureParameterValues" && prop is ArrayPropertyData textureArray)
                            {
                                foreach (var item in textureArray.Value)
                                {
                                    if (item is StructPropertyData structProp)
                                    {
                                        string pName = "", pValue = "";
                                        foreach (var field in structProp.Value)
                                        {
                                            var fn = field.Name.ToString();
                                            if (fn == "ParameterName") pName = GetPropertyValue(field, 0)?.ToString() ?? "";
                                            else if (fn == "ParameterValue" && field is ObjectPropertyData texProp)
                                                pValue = ResolvePackageIndex(asset, texProp.Value);
                                        }
                                        if (!string.IsNullOrEmpty(pName)) textureParams[pName] = pValue;
                                    }
                                }
                            }
                            else if ((propName == "StaticParametersRuntime" || propName == "StaticParameters") && prop is StructPropertyData staticStruct)
                            {
                                foreach (var field in staticStruct.Value)
                                {
                                    if (field.Name.ToString() == "StaticSwitchParameters" && field is ArrayPropertyData switchArray)
                                    {
                                        foreach (var sw in switchArray.Value)
                                        {
                                            if (sw is StructPropertyData swStruct)
                                            {
                                                string swName = ""; bool swValue = false;
                                                foreach (var sf in swStruct.Value)
                                                {
                                                    var sfn = sf.Name.ToString();
                                                    if (sfn == "ParameterInfo" && sf is StructPropertyData infoStruct)
                                                    {
                                                        foreach (var inf in infoStruct.Value)
                                                            if (inf.Name.ToString() == "Name")
                                                                swName = GetPropertyValue(inf, 0)?.ToString() ?? "";
                                                    }
                                                    else if (sfn == "Value")
                                                        swValue = GetPropertyValue(sf, 0)?.ToString()?.ToLower() == "true";
                                                }
                                                if (!string.IsNullOrEmpty(swName)) staticSwitches[swName] = swValue;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
        
                    // Collect refs
                    var refs = CollectAssetRefs(asset);
        
                    results.Add(JsonSerializer.Serialize(new {
                        path,
                        name = matName,
                        is_instance = isInstance,
                        parent,
                        domain,
                        blend_mode = blendMode,
                        shading_model = shadingModel,
                        scalar_params = scalarParams,
                        vector_params = vectorParams,
                        texture_params = textureParams,
                        static_switches = staticSwitches,
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

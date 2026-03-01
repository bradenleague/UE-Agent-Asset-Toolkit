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

namespace AssetParser.Core
{
    public static class Helpers
    {
        
        /// <summary>
        /// Resolve an FPackageIndex (ObjectProperty value) to a human-readable path or class reference.
        /// - Imports: walk outer chain to find the package path. For /Script/ imports, returns
        ///   tuple format "(, /Script/Module.ClassName, )" so Python's _extract_path_from_ref works.
        ///   For all other / paths (plugin mounts like /ShooterCore/, /Game/, etc.), returns the package path.
        /// - Exports: returns the export's ObjectName (local reference within the same asset).
        /// - Index 0: returns null.
        /// </summary>
        public static object? ResolveObjectRef(FPackageIndex index)
        {
            if (index == null || index.Index == 0) return null;
            if (ProgramContext.currentAsset == null) return index.Index;
        
            try
            {
                if (index.IsImport())
                {
                    var import = index.ToImport(ProgramContext.currentAsset);
                    if (import == null) return index.Index;
        
                    var objectName = import.ObjectName.ToString();
        
                    // Walk up the outer chain to find the package
                    var currentIdx = import.OuterIndex;
                    while (currentIdx.Index != 0)
                    {
                        if (currentIdx.IsImport())
                        {
                            var outerImport = ProgramContext.currentAsset.Imports[-currentIdx.Index - 1];
                            if (outerImport.ClassName.ToString() == "Package")
                            {
                                var pkgName = outerImport.ObjectName.ToString();
                                if (pkgName.StartsWith("/Script/"))
                                {
                                    // Return tuple format for class references
                                    return $"(, {pkgName}.{objectName}, )";
                                }
                                if (pkgName.StartsWith("/"))
                                {
                                    // Plugin mounts, /Game/, etc. — return asset path
                                    return pkgName;
                                }
                            }
                            currentIdx = outerImport.OuterIndex;
                        }
                        else
                        {
                            break;
                        }
                    }
        
                    // Check if the import itself is a Package
                    if (import.ClassName.ToString() == "Package")
                    {
                        var pkgName = import.ObjectName.ToString();
                        if (pkgName.StartsWith("/Script/"))
                            return $"(, {pkgName}, )";
                        if (pkgName.StartsWith("/"))
                            return pkgName;
                    }
        
                    // Couldn't resolve to a path — return the object name as fallback
                    return objectName;
                }
                else if (index.IsExport())
                {
                    var export = index.ToExport(ProgramContext.currentAsset);
                    return export?.ObjectName.ToString() ?? (object)index.Index;
                }
            }
            catch
            {
                // Fall through
            }
        
            return index.Index;
        }
        
        public static string GetTextPropertyValue(TextPropertyData textProp)
        {
            // Prefer CultureInvariantString (the actual source text) for Base/None history types.
            // textProp.Value is often just the 32-char hex localization key.
            var culture = textProp.CultureInvariantString?.ToString();
            if (!string.IsNullOrEmpty(culture))
                return culture;
        
            var val = textProp.Value?.ToString();
            if (string.IsNullOrEmpty(val))
                return "";
        
            // Filter out 32-char hex localization keys (e.g. "A1B2C3D4E5F6...")
            if (System.Text.RegularExpressions.Regex.IsMatch(val, @"^[0-9A-Fa-f]{32}$"))
                return "";
        
            return val;
        }
        
        public static object GetPropertyValue(PropertyData prop, int depth)
        {
            if (depth > 3) return "[max depth]";
        
            try
            {
                return prop switch
                {
                    StrPropertyData strProp => strProp.Value?.ToString() ?? "",
                    TextPropertyData textProp => GetTextPropertyValue(textProp),
                    IntPropertyData intProp => intProp.Value,
                    Int64PropertyData int64Prop => int64Prop.Value,
                    FloatPropertyData floatProp => floatProp.Value,
                    DoublePropertyData doubleProp => doubleProp.Value,
                    BoolPropertyData boolProp => boolProp.Value,
                    NamePropertyData nameProp => nameProp.Value.ToString(),
                    ObjectPropertyData objProp => ResolveObjectRef(objProp.Value) ?? (object)"null",
                    SoftObjectPropertyData softProp => softProp.Value.ToString(),
                    EnumPropertyData enumProp => enumProp.Value.ToString(),
                    BytePropertyData byteProp => byteProp.Value.ToString(),
        
                    StructPropertyData structProp => ExtractStruct(structProp, depth),
                    GameplayTagContainerPropertyData tagContainer => ExtractGameplayTagContainer(tagContainer),
                    SetPropertyData setProp => $"[Set: {(setProp.Value != null ? setProp.Value.Length : 0)} items]",
                    ArrayPropertyData arrayProp => ExtractArray(arrayProp, depth),
                    MapPropertyData mapProp => $"[Map: {(mapProp.Value != null ? mapProp.Value.Count : 0)} entries]",
        
                    _ => prop.ToString() ?? "[unknown]"
                };
            }
            catch
            {
                return "[error]";
            }
        }
        
        public static object ExtractStruct(StructPropertyData structProp, int depth)
        {
            if (structProp.Value == null || structProp.Value.Count == 0)
                return $"[Struct: {structProp.StructType}]";
        
            var structData = new Dictionary<string, object>
            {
                ["_type"] = structProp.StructType?.ToString() ?? "Unknown"
            };
        
            foreach (var prop in structProp.Value)
            {
                structData[prop.Name.ToString()] = GetPropertyValue(prop, depth + 1);
            }
        
            return structData;
        }
        
        public static object ExtractArray(ArrayPropertyData arrayProp, int depth)
        {
            if (arrayProp.Value == null || arrayProp.Value.Length == 0)
                return new List<object>();
        
            // For large arrays, just return count
            if (arrayProp.Value.Length > 20)
                return $"[Array: {arrayProp.Value.Length} items]";
        
            var items = new List<object>();
            foreach (var item in arrayProp.Value)
            {
                items.Add(GetPropertyValue(item, depth + 1));
            }
            return items;
        }
        
        public static object ExtractGameplayTagContainer(GameplayTagContainerPropertyData tagContainer)
        {
            var tags = new List<string>();
            if (tagContainer.Value != null)
            {
                foreach (var tag in tagContainer.Value)
                {
                    var tagStr = tag.ToString();
                    if (!string.IsNullOrEmpty(tagStr) && tagStr != "None")
                    {
                        tags.Add(tagStr);
                    }
                }
            }
            return new Dictionary<string, object>
            {
                ["_type"] = "GameplayTagContainer",
                ["tags"] = tags
            };
        }
        
        
    }
}

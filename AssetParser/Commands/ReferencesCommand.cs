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
    public static class ReferencesCommand
    {
        public static void ExtractReferences(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
            xml.AppendLine("<references>");
        
            // Get the main asset name
            var mainExport = asset.Exports.FirstOrDefault();
            var assetName = mainExport?.ObjectName.ToString() ?? "Unknown";
            xml.AppendLine($"  <asset>{EscapeXml(assetName)}</asset>");
        
            // Collect unique asset references from imports
            var assetRefs = new HashSet<string>();
            var classRefs = new HashSet<string>();
            var scriptRefs = new HashSet<string>();
        
            foreach (var import in asset.Imports)
            {
                var objectName = import.ObjectName.ToString();
                var className = import.ClassName.ToString();
                var outerName = import.OuterIndex.Index == 0 ? "" :
                    (import.OuterIndex.IsImport() ? asset.Imports[-import.OuterIndex.Index - 1].ObjectName.ToString() : "");
        
                // Skip internal engine references
                if (objectName.StartsWith("Default__")) continue;
                if (className == "Package" && !objectName.Contains("/Game/")) continue;
        
                // Build the full path if it's a game asset
                string fullPath = "";
        
                // Check if this import or its outer is a Package with /Game/ path
                if (className == "Package" && objectName.Contains("/Game/"))
                {
                    // This is a direct package reference
                    fullPath = objectName;
                }
                else if (!string.IsNullOrEmpty(outerName) && outerName.Contains("/Game/"))
                {
                    // The outer is the package path
                    fullPath = outerName;
                }
                else
                {
                    // Try to find the package in the outer chain
                    var currentIdx = import.OuterIndex;
                    while (currentIdx.Index != 0)
                    {
                        if (currentIdx.IsImport())
                        {
                            var outerImport = asset.Imports[-currentIdx.Index - 1];
                            if (outerImport.ClassName.ToString() == "Package")
                            {
                                var pkgName = outerImport.ObjectName.ToString();
                                if (pkgName.Contains("/Game/") || pkgName.StartsWith("/Script/"))
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
        
                // Categorize the reference
                if (!string.IsNullOrEmpty(fullPath))
                {
                    if (fullPath.StartsWith("/Game/"))
                    {
                        assetRefs.Add(fullPath);
                    }
                    else if (fullPath.StartsWith("/Script/"))
                    {
                        scriptRefs.Add(fullPath);
                    }
                }
        
                // Track class/type references (for inheritance, interfaces, etc.)
                if (className == "Class" || className == "BlueprintGeneratedClass" || className == "WidgetBlueprintGeneratedClass")
                {
                    classRefs.Add(objectName);
                }
            }
        
            // Also scan exports for ObjectProperty references
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
        
            // Output asset references (other game assets this depends on)
            if (assetRefs.Count > 0)
            {
                xml.AppendLine("  <asset-refs>");
                foreach (var refPath in assetRefs.OrderBy(r => r))
                {
                    xml.AppendLine($"    <ref>{EscapeXml(refPath)}</ref>");
                }
                xml.AppendLine("  </asset-refs>");
            }
        
            // Output class references (parent classes, interfaces)
            if (classRefs.Count > 0)
            {
                xml.AppendLine("  <class-refs>");
                foreach (var classRef in classRefs.OrderBy(r => r))
                {
                    xml.AppendLine($"    <ref>{EscapeXml(classRef)}</ref>");
                }
                xml.AppendLine("  </class-refs>");
            }
        
            // Output script/engine references
            if (scriptRefs.Count > 0)
            {
                xml.AppendLine("  <script-refs>");
                foreach (var scriptRef in scriptRefs.OrderBy(r => r))
                {
                    xml.AppendLine($"    <ref>{EscapeXml(scriptRef)}</ref>");
                }
                xml.AppendLine("  </script-refs>");
            }
        
            xml.AppendLine("</references>");
            Console.WriteLine(xml.ToString());
        }
        
        public static void CollectAssetRefsFromProperty(UAsset asset, PropertyData prop, HashSet<string> assetRefs)
        {
            switch (prop)
            {
                case ObjectPropertyData objProp:
                    var refPath = ResolveAssetPath(asset, objProp.Value);
                    if (!string.IsNullOrEmpty(refPath) && refPath.StartsWith("/Game/"))
                        assetRefs.Add(refPath);
                    break;
        
                case SoftObjectPropertyData softProp:
                    var packageName = softProp.Value.AssetPath.PackageName;
                    if (packageName != null)
                    {
                        var softPath = packageName.ToString();
                        if (!string.IsNullOrEmpty(softPath) && softPath.StartsWith("/Game/"))
                            assetRefs.Add(softPath);
                    }
                    break;
        
                case ArrayPropertyData arrayProp:
                    if (arrayProp.Value != null)
                    {
                        foreach (var item in arrayProp.Value)
                            CollectAssetRefsFromProperty(asset, item, assetRefs);
                    }
                    break;
        
                case StructPropertyData structProp:
                    if (structProp.Value != null)
                    {
                        foreach (var field in structProp.Value)
                            CollectAssetRefsFromProperty(asset, field, assetRefs);
                    }
                    break;
        
                case MapPropertyData mapProp:
                    if (mapProp.Value != null)
                    {
                        foreach (var kvp in mapProp.Value)
                        {
                            CollectAssetRefsFromProperty(asset, kvp.Key, assetRefs);
                            CollectAssetRefsFromProperty(asset, kvp.Value, assetRefs);
                        }
                    }
                    break;
            }
        }
        
        public static string ResolveAssetPath(UAsset asset, FPackageIndex index)
        {
            if (index == null || index.Index == 0) return "";
        
            try
            {
                if (index.IsImport())
                {
                    var import = index.ToImport(asset);
                    if (import == null) return "";
        
                    // Walk up the outer chain to find the package
                    var currentIdx = import.OuterIndex;
                    while (currentIdx.Index != 0)
                    {
                        if (currentIdx.IsImport())
                        {
                            var outerImport = asset.Imports[-currentIdx.Index - 1];
                            if (outerImport.ClassName.ToString() == "Package")
                            {
                                var pkgName = outerImport.ObjectName.ToString();
                                if (pkgName.StartsWith("/Game/"))
                                    return pkgName;
                            }
                            currentIdx = outerImport.OuterIndex;
                        }
                        else
                        {
                            break;
                        }
                    }
        
                    // Check if the import itself is from a game package
                    if (import.ClassName.ToString() == "Package")
                    {
                        var pkgName = import.ObjectName.ToString();
                        if (pkgName.StartsWith("/Game/"))
                            return pkgName;
                    }
                }
                else if (index.IsExport())
                {
                    // Same asset reference - not needed for cross-asset refs
                    return "";
                }
            }
            catch
            {
                // Fall through
            }
        
            return "";
        }
        
        
    }
}

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.PropertyTypes.Objects;
using UAssetAPI.PropertyTypes.Structs;
using UAssetAPI.UnrealTypes;
using AssetParser.Core;
using AssetParser.Parsers;
using static AssetParser.Core.Helpers;
using static AssetParser.Core.AssetRefHelper;
using static AssetParser.Commands.GraphCommand;
using static AssetParser.Parsers.ControlFlowAnalyzer;
using static AssetParser.Parsers.BytecodeAnalyzer;

namespace AssetParser.Commands
{
    public static class GraphPlusCommand
    {
        private sealed class ResolvedMemberType
        {
            public string DeclaredType { get; set; } = "auto";
            public string? Container { get; set; }
            public string? KeyType { get; set; }
            public string? ValueType { get; set; }
            public List<string> RawTokens { get; set; } = new();
        }

        private sealed class FunctionSignature
        {
            public List<Dictionary<string, object?>> Parameters { get; set; } = new();
            public Dictionary<string, object?>? Return { get; set; }
        }

        private static readonly Dictionary<string, string> PropertySpecifierMap =
            new(StringComparer.Ordinal)
            {
                ["CPF_Edit"] = "EditAnywhere",
                ["CPF_BlueprintVisible"] = "BlueprintReadWrite",
                ["CPF_BlueprintReadOnly"] = "BlueprintReadOnly",
                ["CPF_BlueprintAssignable"] = "BlueprintAssignable",
                ["CPF_BlueprintCallable"] = "BlueprintCallable",
                ["CPF_Net"] = "Replicated",
                ["CPF_RepNotify"] = "ReplicatedUsing",
                ["CPF_RepSkip"] = "RepSkip",
                ["CPF_Transient"] = "Transient",
                ["CPF_Config"] = "Config",
                ["CPF_GlobalConfig"] = "GlobalConfig",
                ["CPF_SaveGame"] = "SaveGame",
                ["CPF_Interp"] = "Interp",
                ["CPF_InstancedReference"] = "Instanced",
                ["CPF_ContainsInstancedReference"] = "ContainsInstancedReference",
                ["CPF_AdvancedDisplay"] = "AdvancedDisplay",
                ["CPF_ExposeOnSpawn"] = "ExposeOnSpawn",
                ["CPF_DisableEditOnInstance"] = "EditDefaultsOnly",
                ["CPF_DisableEditOnTemplate"] = "EditInstanceOnly",
                ["CPF_EditConst"] = "EditConst",
                ["CPF_AssetRegistrySearchable"] = "AssetRegistrySearchable",
            };

        private static readonly Dictionary<string, string> FunctionFlagMap =
            new(StringComparer.Ordinal)
            {
                ["FUNC_BlueprintCallable"] = "BlueprintCallable",
                ["FUNC_BlueprintPure"] = "BlueprintPure",
                ["FUNC_BlueprintEvent"] = "BlueprintEvent",
                ["FUNC_BlueprintAuthorityOnly"] = "BlueprintAuthorityOnly",
                ["FUNC_BlueprintCosmetic"] = "BlueprintCosmetic",
                ["FUNC_Net"] = "Net",
                ["FUNC_NetReliable"] = "Reliable",
                ["FUNC_NetServer"] = "Server",
                ["FUNC_NetClient"] = "Client",
                ["FUNC_NetMulticast"] = "NetMulticast",
                ["FUNC_Exec"] = "Exec",
                ["FUNC_Static"] = "Static",
                ["FUNC_Const"] = "Const",
                ["FUNC_Public"] = "Public",
                ["FUNC_Private"] = "Private",
                ["FUNC_Protected"] = "Protected",
                ["FUNC_Final"] = "Final",
                ["FUNC_Native"] = "Native",
                ["FUNC_Event"] = "Event",
                ["FUNC_Delegate"] = "Delegate",
                ["FUNC_MulticastDelegate"] = "MulticastDelegate",
            };

        private static readonly Dictionary<string, string> PrimitiveTypeMap =
            new(StringComparer.Ordinal)
            {
                ["BoolProperty"] = "bool",
                ["ByteProperty"] = "uint8",
                ["IntProperty"] = "int32",
                ["Int64Property"] = "int64",
                ["FloatProperty"] = "float",
                ["DoubleProperty"] = "double",
                ["NameProperty"] = "FName",
                ["StrProperty"] = "FString",
                ["TextProperty"] = "FText",
                ["EnumProperty"] = "uint8",
            };

        public static void ExtractGraphPlusJson(UAsset asset)
        {
            var options = new JsonSerializerOptions
            {
                WriteIndented = true,
                DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            };

            var bpName = ResolveBlueprintName(asset);
            var graphJson = TryCaptureGraphJson(asset, out var graphError);
            var defaults = BuildCdoDefaults(asset);
            var members = ExtractMemberDeclarations(asset, defaults);
            var functions = ExtractFunctions(asset, out var signaturesByFunction);
            var delegates = ExtractDelegates(asset, signaturesByFunction);

            var output = new Dictionary<string, object?>
            {
                ["name"] = bpName,
                ["detail"] = "graph",
                ["contract"] = "graph_plus_v1",
                ["graph"] = graphJson,
                ["members"] = members,
                ["functions"] = functions,
                ["delegates"] = delegates,
            };

            if (!string.IsNullOrWhiteSpace(graphError))
            {
                output["graph_error"] = graphError;
            }

            Console.Write(JsonSerializer.Serialize(output, options));
        }

        public static void ExtractGraphSummaryJson(UAsset asset)
        {
            var options = new JsonSerializerOptions
            {
                WriteIndented = true,
                DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            };

            var bpName = ResolveBlueprintName(asset);
            var defaults = BuildCdoDefaults(asset);
            var members = ExtractMemberDeclarations(asset, defaults);
            var functions = ExtractFunctions(asset, out var signaturesByFunction);
            var delegates = ExtractDelegates(asset, signaturesByFunction);

            var output = new Dictionary<string, object?>
            {
                ["name"] = bpName,
                ["detail"] = "graph-summary",
                ["contract"] = "graph_summary_v1",
                ["members"] = members,
                ["functions"] = functions,
                ["delegates"] = delegates,
            };

            Console.Write(JsonSerializer.Serialize(output, options));
        }

        private static string ResolveBlueprintName(UAsset asset)
        {
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint", StringComparison.Ordinal) == true);

            return bpExport?.ObjectName.ToString()
                ?? Path.GetFileNameWithoutExtension(ProgramContext.assetPath)
                ?? "Unknown";
        }

        private static object? TryCaptureGraphJson(UAsset asset, out string? error)
        {
            error = null;
            var originalOut = Console.Out;

            try
            {
                using var capture = new StringWriter();
                Console.SetOut(capture);
                ExtractGraph(asset, "json");
                Console.Out.Flush();

                var raw = capture.ToString().Trim();
                if (string.IsNullOrEmpty(raw))
                {
                    error = "graph-json capture was empty";
                    return null;
                }

                using var doc = JsonDocument.Parse(raw);
                return doc.RootElement.Clone();
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return null;
            }
            finally
            {
                Console.SetOut(originalOut);
            }
        }

        private static Dictionary<string, object?> BuildCdoDefaults(UAsset asset)
        {
            var defaults = new Dictionary<string, object?>(StringComparer.Ordinal);
            var cdoExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.ObjectName.ToString().StartsWith("Default__", StringComparison.Ordinal));

            if (cdoExport?.Data == null)
                return defaults;

            foreach (var prop in cdoExport.Data)
            {
                var propName = prop.Name.ToString();
                if (string.IsNullOrWhiteSpace(propName) || propName == "None")
                    continue;

                defaults[propName] = GetPropertyValue(prop, 0);
            }

            return defaults;
        }

        private static List<Dictionary<string, object?>> ExtractMemberDeclarations(
            UAsset asset,
            Dictionary<string, object?> defaults)
        {
            var members = new List<Dictionary<string, object?>>();

            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i];
                var className = export.GetExportClassType()?.ToString() ?? "";

                if (!className.EndsWith("Property", StringComparison.Ordinal))
                    continue;

                var propName = export.ObjectName.ToString();
                if (ShouldSkipMemberName(propName) || IsFunctionScoped(asset, export)
                    || IsNestedProperty(asset, export))
                    continue;

                var resolved = ResolveMemberType(asset, i + 1, new HashSet<int>());
                var rawFlags = GetExportFlags(export, "PropertyFlags");
                var specifiers = MapFlags(rawFlags, PropertySpecifierMap);

                var member = new Dictionary<string, object?>
                {
                    ["name"] = propName,
                    ["declared_type"] = resolved.DeclaredType,
                    ["container"] = resolved.Container,
                    ["key_type"] = resolved.KeyType,
                    ["value_type"] = resolved.ValueType,
                    ["specifiers"] = specifiers,
                    ["raw_property_flags"] = rawFlags,
                    ["raw_type_tokens"] = resolved.RawTokens,
                    ["default_value"] = defaults.TryGetValue(propName, out var defaultValue) ? defaultValue : null,
                    ["default_source"] = defaults.ContainsKey(propName) ? "cdo" : "unavailable",
                };

                members.Add(member);
            }

            return members;
        }

        private static List<Dictionary<string, object?>> ExtractFunctions(
            UAsset asset,
            out Dictionary<string, FunctionSignature> signaturesByFunction)
        {
            var functions = new List<Dictionary<string, object?>>();
            signaturesByFunction = new Dictionary<string, FunctionSignature>(StringComparer.Ordinal);
            var seenNames = new HashSet<string>(StringComparer.Ordinal);

            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var funcName = funcExport.ObjectName.ToString();
                if (ShouldSkipFunction(funcName))
                    continue;

                seenNames.Add(funcName);
                var rawFlags = funcExport.FunctionFlags.ToString();
                var mappedFlags = MapFlags(rawFlags, FunctionFlagMap);
                var signature = ExtractFunctionSignature(asset, funcExport);
                signaturesByFunction[funcName] = signature;

                var isEvent = IsEventFunction(funcName, mappedFlags);
                var (metadata, metadataSource) = ExtractFunctionMetadata(funcExport);

                functions.Add(new Dictionary<string, object?>
                {
                    ["name"] = funcName,
                    ["raw_flags"] = rawFlags,
                    ["flags_mapped"] = mappedFlags,
                    ["params"] = signature.Parameters,
                    ["return"] = signature.Return,
                    ["is_event"] = isEvent,
                    ["metadata"] = metadata,
                    ["metadata_source"] = metadataSource,
                });
            }

            // Fallback: scan for RawExport/NormalExport entries with Function class
            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i];
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!string.Equals(className, "Function", StringComparison.Ordinal))
                    continue;

                var funcName = export.ObjectName.ToString();
                if (ShouldSkipFunction(funcName) || seenNames.Contains(funcName))
                    continue;

                seenNames.Add(funcName);
                var signature = ExtractFunctionSignatureFromChildren(asset, i + 1);
                signaturesByFunction[funcName] = signature;

                var isEvent = IsEventFunction(funcName, new List<string>());

                functions.Add(new Dictionary<string, object?>
                {
                    ["name"] = funcName,
                    ["raw_flags"] = "",
                    ["flags_mapped"] = new List<string>(),
                    ["params"] = signature.Parameters,
                    ["return"] = signature.Return,
                    ["is_event"] = isEvent,
                    ["metadata"] = new Dictionary<string, object?>(),
                    ["metadata_source"] = "unavailable",
                });
            }

            return functions;
        }

        private static Dictionary<string, object?> ExtractDelegates(
            UAsset asset,
            Dictionary<string, FunctionSignature> signaturesByFunction)
        {
            var declarations = new List<Dictionary<string, object?>>();
            var declarationByName = new Dictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);

            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i] as NormalExport;
                if (export == null)
                    continue;

                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!(className.EndsWith("DelegateProperty", StringComparison.Ordinal)
                    || className.EndsWith("MulticastDelegateProperty", StringComparison.Ordinal)))
                {
                    continue;
                }

                var delegateName = export.ObjectName.ToString();
                var kind = className.Contains("Multicast", StringComparison.Ordinal) ? "multicast" : "delegate";
                var signatureName = ResolveSignatureFunctionName(asset, export);
                var signaturePayload = BuildSignaturePayload(signaturesByFunction, signatureName);

                var entry = new Dictionary<string, object?>
                {
                    ["name"] = delegateName,
                    ["kind"] = kind,
                    ["signature"] = signaturePayload,
                    ["signature_function"] = signatureName,
                    ["source"] = "property",
                    ["confidence"] = signaturePayload != null ? "high" : "medium",
                    ["raw_type_tokens"] = new List<string>
                    {
                        className,
                        signatureName ?? "",
                    }.Where(s => !string.IsNullOrWhiteSpace(s)).Distinct().ToList(),
                };

                declarationByName[delegateName] = entry;
            }

            // Scan typed FunctionExport entries for delegate signatures
            var seenDelegateSigNames = new HashSet<string>(StringComparer.Ordinal);
            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var funcName = funcExport.ObjectName.ToString();
                if (!funcName.EndsWith("__DelegateSignature", StringComparison.Ordinal))
                    continue;

                seenDelegateSigNames.Add(funcName);
                var delegateName = funcName[..^"__DelegateSignature".Length];
                var flagsMapped = MapFlags(funcExport.FunctionFlags.ToString(), FunctionFlagMap);
                var kind = flagsMapped.Contains("MulticastDelegate") ? "multicast" : "delegate";

                if (!declarationByName.TryGetValue(delegateName, out var entry))
                {
                    entry = new Dictionary<string, object?>
                    {
                        ["name"] = delegateName,
                        ["kind"] = kind,
                        ["source"] = "function_export",
                        ["confidence"] = "medium",
                        ["raw_type_tokens"] = new List<string> { "FunctionExport", funcName },
                    };
                    declarationByName[delegateName] = entry;
                }

                entry["signature"] = BuildSignaturePayload(signaturesByFunction, funcName);
                entry["signature_function"] = funcName;
                if (!Equals(entry["source"], "property"))
                {
                    entry["source"] = "function_export";
                }
                if (entry["signature"] != null)
                {
                    entry["confidence"] = "high";
                }
            }

            // Fallback: scan RawExport/NormalExport with Function class for delegate signatures
            foreach (var export in asset.Exports)
            {
                var expClass = export.GetExportClassType()?.ToString() ?? "";
                if (!string.Equals(expClass, "Function", StringComparison.Ordinal))
                    continue;

                var funcName = export.ObjectName.ToString();
                if (!funcName.EndsWith("__DelegateSignature", StringComparison.Ordinal))
                    continue;
                if (seenDelegateSigNames.Contains(funcName))
                    continue;

                var delegateName = funcName[..^"__DelegateSignature".Length];

                if (!declarationByName.TryGetValue(delegateName, out var entry))
                {
                    entry = new Dictionary<string, object?>
                    {
                        ["name"] = delegateName,
                        ["kind"] = "delegate",
                        ["source"] = "raw_export",
                        ["confidence"] = "low",
                        ["raw_type_tokens"] = new List<string> { "RawExport", funcName },
                    };
                    declarationByName[delegateName] = entry;
                }

                entry["signature"] = BuildSignaturePayload(signaturesByFunction, funcName);
                entry["signature_function"] = funcName;
                if (entry["signature"] != null)
                {
                    entry["confidence"] = "medium";
                }
            }

            declarations.AddRange(declarationByName.Values.OrderBy(d => d["name"]?.ToString()));

            var bindings = new List<Dictionary<string, object?>>();
            var bindingKeys = new HashSet<string>(StringComparer.Ordinal);

            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var observations = CollectDelegateBindings(asset, funcExport);
                foreach (var obs in observations)
                {
                    var key = string.Join("|", obs.OwnerFunction, obs.Operation, obs.DelegateTarget, obs.BoundFunction ?? "", obs.ObjectTerm ?? "");
                    if (!bindingKeys.Add(key))
                        continue;

                    bindings.Add(new Dictionary<string, object?>
                    {
                        ["owner_function"] = obs.OwnerFunction,
                        ["operation"] = obs.Operation,
                        ["delegate_target"] = obs.DelegateTarget,
                        ["bound_function"] = obs.BoundFunction,
                        ["object_term"] = obs.ObjectTerm,
                        ["source"] = obs.Source,
                        ["confidence"] = obs.Confidence,
                    });
                }
            }

            return new Dictionary<string, object?>
            {
                ["declarations"] = declarations,
                ["bindings"] = bindings,
            };
        }

        private static string? ResolveSignatureFunctionName(UAsset asset, NormalExport export)
        {
            var signatureProp = export.Data?.FirstOrDefault(p => p.Name.ToString() == "SignatureFunction");
            if (signatureProp is ObjectPropertyData objProp && objProp.Value.Index != 0)
            {
                var resolved = ResolvePackageIndex(asset, objProp.Value);
                if (!string.IsNullOrWhiteSpace(resolved) && resolved != "[null]")
                    return NormalizeTypeToken(resolved);
            }

            return null;
        }

        private static object? BuildSignaturePayload(
            Dictionary<string, FunctionSignature> signaturesByFunction,
            string? signatureName)
        {
            if (string.IsNullOrWhiteSpace(signatureName))
                return null;

            if (!signaturesByFunction.TryGetValue(signatureName, out var signature))
            {
                var bySuffix = signaturesByFunction.FirstOrDefault(kvp =>
                    string.Equals(kvp.Key, signatureName, StringComparison.Ordinal)
                    || string.Equals(NormalizeTypeToken(kvp.Key), signatureName, StringComparison.Ordinal));

                if (string.IsNullOrWhiteSpace(bySuffix.Key))
                    return null;

                signature = bySuffix.Value;
            }

            return new Dictionary<string, object?>
            {
                ["params"] = signature.Parameters,
                ["return"] = signature.Return,
            };
        }

        private static FunctionSignature ExtractFunctionSignature(UAsset asset, FunctionExport funcExport)
        {
            var signature = new FunctionSignature();

            if (funcExport.LoadedProperties != null && funcExport.LoadedProperties.Length > 0)
            {
                foreach (var prop in funcExport.LoadedProperties)
                {
                    if (!prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_Parm))
                        continue;

                    var param = new Dictionary<string, object?>
                    {
                        ["name"] = prop.Name?.ToString() ?? "Unknown",
                        ["declared_type"] = ResolveSerializedType(prop.SerializedType?.ToString() ?? "Unknown"),
                        ["direction"] = GetParameterDirection(prop.PropertyFlags),
                        ["specifiers"] = MapFlags(prop.PropertyFlags.ToString(), PropertySpecifierMap),
                        ["raw_property_flags"] = prop.PropertyFlags.ToString(),
                        ["raw_type_tokens"] = new List<string> { prop.SerializedType?.ToString() ?? "Unknown" },
                    };

                    if (Equals(param["direction"], "return"))
                        signature.Return = param;
                    else
                        signature.Parameters.Add(param);
                }

                return signature;
            }

            var funcIndex = Array.IndexOf(asset.Exports.ToArray(), funcExport) + 1;
            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!className.EndsWith("Property", StringComparison.Ordinal))
                    continue;

                if (export.OuterIndex.Index != funcIndex)
                    continue;

                signature.Parameters.Add(new Dictionary<string, object?>
                {
                    ["name"] = export.ObjectName.ToString(),
                    ["declared_type"] = ResolveSerializedType(className),
                    ["direction"] = "in",
                    ["specifiers"] = new List<string>(),
                    ["raw_property_flags"] = "",
                    ["raw_type_tokens"] = new List<string> { className },
                });
            }

            return signature;
        }

        private static FunctionSignature ExtractFunctionSignatureFromChildren(UAsset asset, int funcExportIndex)
        {
            var signature = new FunctionSignature();

            foreach (var export in asset.Exports)
            {
                var className = export.GetExportClassType()?.ToString() ?? "";
                if (!className.EndsWith("Property", StringComparison.Ordinal))
                    continue;

                if (export.OuterIndex.Index != funcExportIndex)
                    continue;

                signature.Parameters.Add(new Dictionary<string, object?>
                {
                    ["name"] = export.ObjectName.ToString(),
                    ["declared_type"] = ResolveSerializedType(className),
                    ["direction"] = "in",
                    ["specifiers"] = new List<string>(),
                    ["raw_property_flags"] = "",
                    ["raw_type_tokens"] = new List<string> { className },
                });
            }

            return signature;
        }

        private static (Dictionary<string, object?> metadata, string metadataSource) ExtractFunctionMetadata(FunctionExport funcExport)
        {
            var metadata = new Dictionary<string, object?>(StringComparer.Ordinal);
            var type = funcExport.GetType();

            foreach (var propName in new[] { "Category", "DisplayName", "ToolTip", "CompactNodeTitle", "Keywords" })
            {
                var prop = type.GetProperty(propName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (prop == null)
                    continue;

                var value = prop.GetValue(funcExport)?.ToString();
                if (!string.IsNullOrWhiteSpace(value))
                    metadata[propName] = value;
            }

            foreach (var mapPropName in new[] { "MetaDataMap", "Metadata", "MetaData" })
            {
                var mapProp = type.GetProperty(mapPropName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (mapProp?.GetValue(funcExport) is not IDictionary dict)
                    continue;

                foreach (DictionaryEntry entry in dict)
                {
                    var key = entry.Key?.ToString();
                    if (string.IsNullOrWhiteSpace(key))
                        continue;

                    metadata[key] = entry.Value?.ToString();
                }
            }

            return metadata.Count > 0
                ? (metadata, "function_export")
                : (new Dictionary<string, object?>(), "unavailable");
        }

        private static string GetParameterDirection(EPropertyFlags flags)
        {
            if (flags.HasFlag(EPropertyFlags.CPF_ReturnParm))
                return "return";
            if (flags.HasFlag(EPropertyFlags.CPF_OutParm) && !flags.HasFlag(EPropertyFlags.CPF_ReferenceParm))
                return "out";
            return "in";
        }

        private static bool IsEventFunction(string funcName, List<string> mappedFlags)
        {
            return funcName.StartsWith("Receive", StringComparison.Ordinal)
                || funcName.StartsWith("OnRep_", StringComparison.Ordinal)
                || mappedFlags.Contains("BlueprintEvent")
                || mappedFlags.Contains("Event");
        }

        private static bool ShouldSkipMemberName(string propName)
        {
            return propName.StartsWith("bpv__", StringComparison.Ordinal)
                || propName.StartsWith("K2Node_", StringComparison.Ordinal)
                || propName.StartsWith("Uber", StringComparison.Ordinal)
                || propName == "None";
        }

        private static bool ShouldSkipFunction(string funcName)
        {
            return funcName.StartsWith("bpv__", StringComparison.Ordinal)
                || funcName.StartsWith("__", StringComparison.Ordinal)
                || funcName.StartsWith("InpActEvt_", StringComparison.Ordinal)
                || funcName.StartsWith("InpAxisEvt_", StringComparison.Ordinal)
                || funcName.StartsWith("InpAxisKeyEvt_", StringComparison.Ordinal)
                || funcName.StartsWith("InpTchEvt_", StringComparison.Ordinal)
                || funcName.StartsWith("K2Node_", StringComparison.Ordinal)
                || funcName.Contains("__TRASHFUNC", StringComparison.Ordinal)
                || funcName.Contains("__TRASHEVENT", StringComparison.Ordinal);
        }

        private static bool IsFunctionScoped(UAsset asset, Export export)
        {
            var outer = export.OuterIndex.Index;
            if (outer <= 0 || outer > asset.Exports.Count)
                return false;

            var outerClass = asset.Exports[outer - 1].GetExportClassType()?.ToString() ?? "";
            return outerClass.Contains("Function", StringComparison.Ordinal);
        }

        private static bool IsNestedProperty(UAsset asset, Export export)
        {
            var outer = export.OuterIndex.Index;
            if (outer <= 0 || outer > asset.Exports.Count)
                return false;

            var outerClass = asset.Exports[outer - 1].GetExportClassType()?.ToString() ?? "";
            return outerClass.EndsWith("Property", StringComparison.Ordinal);
        }

        private static ResolvedMemberType ResolveMemberType(UAsset asset, int exportIndex, HashSet<int> visited)
        {
            if (exportIndex <= 0 || exportIndex > asset.Exports.Count)
                return new ResolvedMemberType();

            if (!visited.Add(exportIndex))
                return new ResolvedMemberType { DeclaredType = "auto" };

            var export = asset.Exports[exportIndex - 1];
            var className = export.GetExportClassType()?.ToString() ?? "Unknown";
            var normalExport = export as NormalExport;

            var result = new ResolvedMemberType();
            result.RawTokens.Add(className);

            if (PrimitiveTypeMap.TryGetValue(className, out var primitiveType))
            {
                result.DeclaredType = primitiveType;

                if (className == "EnumProperty")
                {
                    var enumType = ResolveTypeFromPropertyData(asset, normalExport, visited, "Enum", "EnumClass");
                    if (!string.IsNullOrWhiteSpace(enumType.resolvedType))
                        result.DeclaredType = enumType.resolvedType!;
                    if (!string.IsNullOrWhiteSpace(enumType.rawToken))
                        result.RawTokens.Add(enumType.rawToken!);
                }

                return result;
            }

            if (string.Equals(className, "StructProperty", StringComparison.Ordinal))
            {
                var structType = ResolveTypeFromPropertyData(asset, normalExport, visited, "Struct", "StructClass", "StructType");
                if (!string.IsNullOrWhiteSpace(structType.resolvedType))
                    result.DeclaredType = structType.resolvedType!;
                else
                    result.DeclaredType = "UScriptStruct";

                if (!string.IsNullOrWhiteSpace(structType.rawToken))
                    result.RawTokens.Add(structType.rawToken!);
                return result;
            }

            if (string.Equals(className, "ArrayProperty", StringComparison.Ordinal))
            {
                var inner = ResolveTypeFromPropertyData(asset, normalExport, visited, "Inner", "InnerProperty", "ElementProp");
                var innerType = inner.resolvedType ?? "auto";
                result.DeclaredType = $"TArray<{innerType}>";
                result.Container = "array";
                result.ValueType = innerType;
                if (!string.IsNullOrWhiteSpace(inner.rawToken))
                    result.RawTokens.Add(inner.rawToken!);
                return result;
            }

            if (string.Equals(className, "SetProperty", StringComparison.Ordinal))
            {
                var elem = ResolveTypeFromPropertyData(asset, normalExport, visited, "ElementProp", "Inner");
                var elemType = elem.resolvedType ?? "auto";
                result.DeclaredType = $"TSet<{elemType}>";
                result.Container = "set";
                result.ValueType = elemType;
                if (!string.IsNullOrWhiteSpace(elem.rawToken))
                    result.RawTokens.Add(elem.rawToken!);
                return result;
            }

            if (string.Equals(className, "MapProperty", StringComparison.Ordinal))
            {
                var key = ResolveTypeFromPropertyData(asset, normalExport, visited, "KeyProp", "MapKeyProp", "Inner");
                var value = ResolveTypeFromPropertyData(asset, normalExport, visited, "ValueProp", "MapValueProp");
                var keyType = key.resolvedType ?? "auto";
                var valueType = value.resolvedType ?? "auto";
                result.DeclaredType = $"TMap<{keyType}, {valueType}>";
                result.Container = "map";
                result.KeyType = keyType;
                result.ValueType = valueType;
                if (!string.IsNullOrWhiteSpace(key.rawToken))
                    result.RawTokens.Add(key.rawToken!);
                if (!string.IsNullOrWhiteSpace(value.rawToken))
                    result.RawTokens.Add(value.rawToken!);
                return result;
            }

            if (string.Equals(className, "ObjectProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "PropertyClass", "Class", "MetaClass");
                var objType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TObjectPtr<{objType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "WeakObjectProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "PropertyClass", "Class", "MetaClass");
                var objType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TWeakObjectPtr<{objType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "LazyObjectProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "PropertyClass", "Class", "MetaClass");
                var objType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TLazyObjectPtr<{objType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "SoftObjectProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "PropertyClass", "Class", "MetaClass");
                var objType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TSoftObjectPtr<{objType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "ClassProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "MetaClass", "PropertyClass", "Class");
                var classType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TSubclassOf<{classType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "SoftClassProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "MetaClass", "PropertyClass", "Class");
                var classType = target.resolvedType ?? "UObject";
                result.DeclaredType = $"TSoftClassPtr<{classType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "InterfaceProperty", StringComparison.Ordinal))
            {
                var target = ResolveTypeFromPropertyData(asset, normalExport, visited, "InterfaceClass", "PropertyClass", "Class");
                var interfaceType = target.resolvedType ?? "UInterface";
                result.DeclaredType = $"TScriptInterface<{interfaceType}>";
                if (!string.IsNullOrWhiteSpace(target.rawToken))
                    result.RawTokens.Add(target.rawToken!);
                return result;
            }

            if (string.Equals(className, "DelegateProperty", StringComparison.Ordinal)
                || string.Equals(className, "MulticastDelegateProperty", StringComparison.Ordinal))
            {
                var sig = ResolveTypeFromPropertyData(asset, normalExport, visited, "SignatureFunction");
                var sigType = sig.resolvedType ?? "UnknownSignature";
                var prefix = className.Contains("Multicast", StringComparison.Ordinal) ? "FMulticastScriptDelegate" : "FScriptDelegate";
                result.DeclaredType = $"{prefix}/*{sigType}*/";
                if (!string.IsNullOrWhiteSpace(sig.rawToken))
                    result.RawTokens.Add(sig.rawToken!);
                return result;
            }

            result.DeclaredType = ResolveSerializedType(className);
            return result;
        }

        private static (string? resolvedType, string? rawToken) ResolveTypeFromPropertyData(
            UAsset asset,
            NormalExport? export,
            HashSet<int> visited,
            params string[] propertyNames)
        {
            if (export?.Data == null)
                return (null, null);

            foreach (var name in propertyNames)
            {
                var prop = export.Data.FirstOrDefault(p => p.Name.ToString() == name);
                if (prop == null)
                    continue;

                if (prop is ObjectPropertyData objProp && objProp.Value.Index != 0)
                {
                    var rawToken = ResolvePackageIndex(asset, objProp.Value);

                    if (objProp.Value.IsExport() && objProp.Value.Index > 0 && objProp.Value.Index <= asset.Exports.Count)
                    {
                        var nestedExport = asset.Exports[objProp.Value.Index - 1];
                        var nestedClass = nestedExport.GetExportClassType()?.ToString() ?? "";
                        if (nestedClass.EndsWith("Property", StringComparison.Ordinal))
                        {
                            var nestedType = ResolveMemberType(asset, objProp.Value.Index, visited);
                            return (nestedType.DeclaredType, rawToken);
                        }
                    }

                    return (NormalizeTypeToken(rawToken), rawToken);
                }

                if (prop is NamePropertyData nameProp)
                {
                    var rawToken = nameProp.Value.ToString();
                    return (NormalizeTypeToken(rawToken), rawToken);
                }

                if (prop is StrPropertyData strProp)
                {
                    var rawToken = strProp.Value?.ToString();
                    if (!string.IsNullOrWhiteSpace(rawToken))
                        return (NormalizeTypeToken(rawToken), rawToken);
                }
            }

            return (null, null);
        }

        private static string ResolveSerializedType(string rawType)
        {
            if (string.IsNullOrWhiteSpace(rawType))
                return "Unknown";

            if (rawType.EndsWith("Property", StringComparison.Ordinal))
            {
                if (PrimitiveTypeMap.TryGetValue(rawType, out var primitiveType))
                    return primitiveType;

                return rawType.Replace("Property", "", StringComparison.Ordinal);
            }

            return NormalizeTypeToken(rawType);
        }

        private static string NormalizeTypeToken(string? token)
        {
            if (string.IsNullOrWhiteSpace(token))
                return "Unknown";

            var cleaned = token.Trim();

            if (cleaned.StartsWith("(, ", StringComparison.Ordinal) && cleaned.EndsWith(", )", StringComparison.Ordinal))
            {
                cleaned = cleaned[3..^3];
            }

            if (cleaned.EndsWith("_C", StringComparison.Ordinal))
                cleaned = cleaned[..^2];

            if (cleaned.StartsWith("/Script/", StringComparison.Ordinal))
            {
                var dot = cleaned.LastIndexOf('.');
                if (dot >= 0 && dot + 1 < cleaned.Length)
                    return cleaned[(dot + 1)..];

                var slash = cleaned.LastIndexOf('/');
                if (slash >= 0 && slash + 1 < cleaned.Length)
                    return cleaned[(slash + 1)..];
            }

            if (cleaned.StartsWith("/", StringComparison.Ordinal))
            {
                var slash = cleaned.LastIndexOf('/');
                if (slash >= 0 && slash + 1 < cleaned.Length)
                    cleaned = cleaned[(slash + 1)..];
            }

            if (cleaned.Contains('.', StringComparison.Ordinal))
            {
                cleaned = cleaned[(cleaned.LastIndexOf('.') + 1)..];
            }

            return cleaned;
        }

        private static string GetExportFlags(Export export, string propertyName)
        {
            var prop = export.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return prop?.GetValue(export)?.ToString() ?? string.Empty;
        }

        private static List<string> MapFlags(string rawFlags, Dictionary<string, string> map)
        {
            if (string.IsNullOrWhiteSpace(rawFlags))
                return new List<string>();

            var output = new List<string>();
            foreach (var rawName in ExtractFlagNames(rawFlags))
            {
                if (map.TryGetValue(rawName, out var mapped))
                {
                    if (!output.Contains(mapped, StringComparer.Ordinal))
                        output.Add(mapped);
                    continue;
                }

                var noPrefix = rawName;
                if (noPrefix.StartsWith("CPF_", StringComparison.Ordinal)
                    || noPrefix.StartsWith("FUNC_", StringComparison.Ordinal))
                {
                    noPrefix = noPrefix[4..];
                }

                if (map.TryGetValue(noPrefix, out mapped) && !output.Contains(mapped, StringComparer.Ordinal))
                {
                    output.Add(mapped);
                }
            }

            return output;
        }

        private static IEnumerable<string> ExtractFlagNames(string rawFlags)
        {
            return rawFlags
                .Split(new[] { ',', '|' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(s => s.Trim())
                .Where(s => !string.IsNullOrWhiteSpace(s));
        }
    }
}

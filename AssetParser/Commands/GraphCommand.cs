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
    public static class GraphCommand
    {
        
        // --- Pin binary reader helpers ---
        
        public static string ReadFNameStr(BinaryReader r, IReadOnlyList<FString> nameMap)
        {
            int idx = r.ReadInt32();
            int num = r.ReadInt32();
            if (idx < 0 || idx >= nameMap.Count) return $"[idx:{idx}]";
            string name = nameMap[idx].ToString();
            if (num > 0) name += $"_{num - 1}";
            return name;
        }
        
        public static string ReadFString(BinaryReader r)
        {
            int len = r.ReadInt32();
            if (len == 0) return "";
            if (len > 0)
            {
                var bytes = r.ReadBytes(len);
                return System.Text.Encoding.UTF8.GetString(bytes).TrimEnd('\0');
            }
            else
            {
                int charCount = -len;
                var bytes = r.ReadBytes(charCount * 2);
                return System.Text.Encoding.Unicode.GetString(bytes).TrimEnd('\0');
            }
        }
        
        // Read FText: uint32 Flags + int8 HistoryType + type-specific data
        // Source: Text.cpp FText::SerializeText, TextHistory.cpp for each type
        // Supported types: -1(None), 0(Base), 1(NamedFormat), 2(OrderedFormat),
        //   3(ArgumentFormat), 10(Transform), 11(StringTableEntry)
        // NOT yet supported: 4(AsDateTime), 5(AsDate), 6(AsTime), 7(AsNumber),
        //   8(AsCurrency), 9(AsPercent) — these require reading recursive FText +
        //   format options. Add them here if you hit "Unsupported FText HistoryType N".
        public static string ReadFText(BinaryReader r)
        {
            uint flags = r.ReadUInt32();
            sbyte historyType = r.ReadSByte();
        
            switch (historyType)
            {
                case -1: // None
                {
                    // bool bHasCultureInvariantString (serialized as uint32)
                    uint hasCultureInvariant = r.ReadUInt32();
                    if (hasCultureInvariant != 0)
                    {
                        return ReadFString(r);
                    }
                    return "";
                }
                case 0: // Base
                {
                    string ns = ReadFString(r);    // Namespace
                    string key = ReadFString(r);   // Key
                    string src = ReadFString(r);   // SourceString
                    return src;
                }
                case 1: // NamedFormat
                case 2: // OrderedFormat
                case 3: // ArgumentFormat
                {
                    // FormatText (recursive FText)
                    string fmtText = ReadFText(r);
                    // Arguments: TMap<FString, FFormatArgumentValue>
                    int argCount = r.ReadInt32();
                    for (int a = 0; a < argCount; a++)
                    {
                        ReadFString(r); // key
                        ReadFormatArgumentValue(r);
                    }
                    return fmtText;
                }
                case 10: // Transform
                {
                    ReadFText(r);   // SourceText
                    r.ReadByte();   // TransformType (uint8)
                    return "";
                }
                case 11: // StringTableEntry
                {
                    string tableId = ReadFString(r);
                    string key = ReadFString(r);
                    return $"[ST:{tableId}/{key}]";
                }
                default:
                {
                    throw new FormatException($"Unsupported FText HistoryType {historyType} at position {r.BaseStream.Position}");
                }
            }
        }
        
        // Read FFormatArgumentValue: int8 TypeIndex + type-specific data
        public static void ReadFormatArgumentValue(BinaryReader r)
        {
            sbyte typeIdx = r.ReadSByte();
            switch (typeIdx)
            {
                case 0: r.ReadInt64(); break;   // Int
                case 1: r.ReadUInt64(); break;  // UInt
                case 2: r.ReadSingle(); break;  // Float
                case 3: r.ReadDouble(); break;  // Double
                case 4: ReadFText(r); break;    // Text (recursive)
                case 5: r.ReadSByte(); break;   // Gender (ETextGender)
                default: throw new FormatException($"Unknown FFormatArgumentValue type {typeIdx}");
            }
        }
        
        public static Guid ReadFGuid(BinaryReader r)
        {
            return new Guid(r.ReadBytes(16));
        }
        
        // Read a pin reference (from LinkedTo, SubPins, ParentPin, RefPassThrough)
        // Returns (owningNodeExportIndex, pinGuid) or null if null ref
        public static (int nodeExportIndex, Guid pinGuid)? ReadPinRef(BinaryReader r)
        {
            uint isNull = r.ReadUInt32();
            if (isNull != 0) return null;
            int nodeRef = r.ReadInt32(); // FPackageIndex: positive = export index
            var pinGuid = ReadFGuid(r);
            return (nodeRef, pinGuid);
        }
        
        // Read FEdGraphTerminalType (for Map value types — only present when ContainerType == Map)
        // Source: EdGraphPin.cpp FEdGraphTerminalType::Serialize
        public static void ReadTerminalType(BinaryReader r, IReadOnlyList<FString> nameMap)
        {
            ReadFNameStr(r, nameMap);  // TerminalCategory
            ReadFNameStr(r, nameMap);  // TerminalSubCategory
            r.ReadInt32();             // TerminalSubCategoryObject (UObject*)
            r.ReadUInt32();            // bTerminalIsConst (bool as uint32)
            r.ReadUInt32();            // bTerminalIsWeakPointer (bool as uint32)
            r.ReadUInt32();            // bTerminalIsUObjectWrapper (UE5+ only, bool as uint32)
        }
        
        // Read FSimpleMemberReference
        public static void ReadSimpleMemberRef(BinaryReader r, IReadOnlyList<FString> nameMap)
        {
            r.ReadInt32();             // MemberParent (UObject*)
            ReadFNameStr(r, nameMap);  // MemberName (FName)
            ReadFGuid(r);              // MemberGuid (FGuid)
        }
        
        // Reads one pin from the binary Extras blob of a K2Node export.
        // Format derived from UE 5.7 source: EdGraphPin.cpp (Pin::Serialize, FEdGraphPinType::Serialize)
        // and EdGraphNode.cpp (UEdGraphNode::SerializeAsOwningNode).
        //
        // VERSION SENSITIVITY: This assumes editor-saved (WITH_EDITOR) assets. Cooked/packaged
        // builds omit PinFriendlyName, PersistentGuid, BitField, and bSerializeAsSinglePrecisionFloat.
        // If adapting for cooked assets, skip those fields.
        //
        // UE VERSION NOTES (fields that vary by engine version):
        //   - bSerializeAsSinglePrecisionFloat: Added ~5.4-5.7 behind
        //     FUE5ReleaseStreamObjectVersion::SerializeFloatPinDefaultValuesAsSinglePrecision.
        //     If pins fail at "PinType.bSerializeAsSinglePrecisionFloat", remove that ReadUInt32().
        //   - bTerminalIsUObjectWrapper (in ReadTerminalType): UE5+ only.
        //   - SourceIndex: Conditional in source (only serialized when >=0), but appears always
        //     present in editor assets we've tested (5.5, 5.7).
        //
        // DEBUGGING: If pin parsing fails, the exception includes the field name, pin name, and
        // stream position. Compare stream position against a hex dump of the Extras blob to find
        // where the format diverges.
        public static ParsedPin ReadOnePin(BinaryReader r, UAsset asset, IReadOnlyList<FString> nameMap)
        {
            var pin = new ParsedPin();
            pin.LinkedTo = new List<(int, Guid)>();
            long pinStart = r.BaseStream.Position;
            string lastField = "start";
        
            try
            {
                // --- From SerializePin wrapper (EdGraphNode.cpp: SerializeAsOwningNode) ---
                lastField = "bNullPtr";
                uint bNullPtr = r.ReadUInt32();
                if (bNullPtr != 0) throw new FormatException($"Unexpected null pin in owning array (bNullPtr={bNullPtr})");
        
                lastField = "SerializePin.OwningNode";
                r.ReadInt32();
        
                lastField = "SerializePin.PinGuid";
                ReadFGuid(r);
        
                // --- From Pin->Serialize ---
                lastField = "Serialize.OwningNode";
                r.ReadInt32();
        
                lastField = "Serialize.PinId";
                pin.PinId = ReadFGuid(r);
        
                lastField = "PinName";
                pin.Name = ReadFNameStr(r, nameMap);
        
                lastField = "PinFriendlyName";
                ReadFText(r); // WITH_EDITOR only — omitted in cooked builds
        
                lastField = "SourceIndex";
                r.ReadInt32();
        
                lastField = "PinToolTip";
                ReadFString(r);
        
                lastField = "Direction";
                byte dir = r.ReadByte();
                pin.Direction = dir == 0 ? "in" : "out";
        
                // --- FEdGraphPinType ---
                lastField = "PinType.PinCategory";
                pin.Category = ReadFNameStr(r, nameMap);
        
                lastField = "PinType.PinSubCategory";
                pin.SubCategory = ReadFNameStr(r, nameMap);
        
                lastField = "PinType.PinSubCategoryObject";
                int subCatObj = r.ReadInt32();
                pin.SubCategoryObject = subCatObj != 0
                    ? ResolvePackageIndex(asset, new FPackageIndex(subCatObj))
                    : "";
        
                lastField = "PinType.ContainerType";
                pin.ContainerType = r.ReadByte(); // EPinContainerType: 0=None, 1=Array, 2=Set, 3=Map
                if (pin.ContainerType == 3) // Map: read PinValueType (FEdGraphTerminalType)
                {
                    lastField = "PinType.PinValueType";
                    ReadTerminalType(r, nameMap);
                }
        
                lastField = "PinType.bIsReference";
                r.ReadUInt32();
        
                lastField = "PinType.bIsWeakPointer";
                r.ReadUInt32();
        
                lastField = "PinType.MemberRef";
                ReadSimpleMemberRef(r, nameMap);
        
                lastField = "PinType.bIsConst";
                r.ReadUInt32();
        
                lastField = "PinType.bIsUObjectWrapper";
                r.ReadUInt32();
        
                // UE 5.4+: bSerializeAsSinglePrecisionFloat (WITH_EDITOR + custom-version-gated)
                // Source: EdGraphPin.cpp, gated by
                // FUE5ReleaseStreamObjectVersion::SerializeFloatPinDefaultValuesAsSinglePrecision
                if (asset.GetCustomVersion<FUE5ReleaseStreamObjectVersion>()
                    >= FUE5ReleaseStreamObjectVersion.SerializeFloatPinDefaultValuesAsSinglePrecision)
                {
                    lastField = "PinType.bSerializeAsSinglePrecisionFloat";
                    r.ReadUInt32();
                }
        
                // --- Values ---
                lastField = "DefaultValue";
                pin.DefaultValue = ReadFString(r);
        
                lastField = "AutogeneratedDefaultValue";
                pin.AutoDefault = ReadFString(r);
        
                lastField = "DefaultObject";
                r.ReadInt32();
        
                lastField = "DefaultTextValue";
                ReadFText(r);
        
                // --- LinkedTo array ---
                lastField = "LinkedTo.Count";
                int linkedCount = r.ReadInt32();
                for (int i = 0; i < linkedCount; i++)
                {
                    lastField = $"LinkedTo[{i}]";
                    var lref = ReadPinRef(r);
                    if (lref != null) pin.LinkedTo.Add(lref.Value);
                }
        
                // --- SubPins array ---
                lastField = "SubPins.Count";
                int subPinCount = r.ReadInt32();
                for (int i = 0; i < subPinCount; i++)
                {
                    lastField = $"SubPins[{i}]";
                    ReadPinRef(r);
                }
        
                lastField = "ParentPin";
                ReadPinRef(r);
        
                lastField = "RefPassThrough";
                ReadPinRef(r);
        
                // --- Editor-only tail (WITH_EDITOR — omitted in cooked builds) ---
                lastField = "PersistentGuid";
                ReadFGuid(r);
        
                lastField = "BitField";
                uint bitField = r.ReadUInt32(); // bHidden(0), bNotConnectable(1), bDefaultValueIsReadOnly(2), bDefaultValueIsIgnored(3), bAdvancedView(4), bOrphanedPin(5)
                pin.IsHidden = (bitField & (1 << 0)) != 0;
                pin.IsOrphaned = (bitField & (1 << 5)) != 0;
            }
            catch (Exception ex)
            {
                long failPos = r.BaseStream.Position;
                throw new FormatException(
                    $"Pin parse failed at field '{lastField}', pin '{pin.Name ?? "?"}', " +
                    $"stream pos {failPos}/{r.BaseStream.Length} (pin started at {pinStart}): {ex.Message}");
            }
        
            return pin;
        }
        
        public static void ExtractGraph(UAsset asset, string outputFormat)
        {
            var nameMap = asset.GetNameMapIndexList();
        
            // --- Node identity lookup table ---
            // Maps K2Node type → property names to check for a human-readable target label
            var nodeTargetProps = new Dictionary<string, string[]>
            {
                ["K2Node_CallFunction"] = new[] { "FunctionReference" },
                ["K2Node_VariableGet"] = new[] { "VariableReference" },
                ["K2Node_VariableSet"] = new[] { "VariableReference" },
                ["K2Node_DynamicCast"] = new[] { "TargetType" },
                ["K2Node_CustomEvent"] = new[] { "CustomFunctionName" },
                ["K2Node_MacroInstance"] = new[] { "MacroGraphReference" },
                ["K2Node_Event"] = new[] { "EventReference" },
                ["K2Node_ComponentBoundEvent"] = new[] { "DelegatePropertyName" },
                ["K2Node_CallDelegate"] = new[] { "DelegateReference" },
                ["K2Node_CreateDelegate"] = new[] { "SelectedFunctionName" },
            };
        
            string ResolveNodeTarget(NormalExport node, string nodeType)
            {
                if (!nodeTargetProps.TryGetValue(nodeType, out var propNames)) return null;
        
                foreach (var propName in propNames)
                {
                    var prop = node.Data?.FirstOrDefault(p => p.Name.ToString() == propName);
                    if (prop == null) continue;
        
                    // For struct properties (FunctionReference, VariableReference, etc.)
                    // look for MemberName inside
                    if (prop is StructPropertyData structProp)
                    {
                        var memberName = structProp.Value?.FirstOrDefault(p => p.Name.ToString() == "MemberName");
                        if (memberName != null)
                        {
                            var val = memberName.ToString();
                            if (!string.IsNullOrEmpty(val) && val != "None") return val;
                        }
                        // Try MemberParent for the class name
                        var memberParent = structProp.Value?.FirstOrDefault(p => p.Name.ToString() == "MemberParent");
                        if (memberParent is ObjectPropertyData objProp && objProp.Value != null && objProp.Value.Index != 0)
                        {
                            return ResolvePackageIndex(asset, objProp.Value);
                        }
                    }
                    // For name/string properties
                    else if (prop is NamePropertyData nameProp)
                    {
                        var val = nameProp.Value?.ToString();
                        if (!string.IsNullOrEmpty(val) && val != "None") return val;
                    }
                    else if (prop is StrPropertyData strProp)
                    {
                        var val = strProp.Value?.ToString();
                        if (!string.IsNullOrEmpty(val)) return val;
                    }
                    // For object references (TargetType on DynamicCast)
                    else if (prop is ObjectPropertyData objProp2 && objProp2.Value != null && objProp2.Value.Index != 0)
                    {
                        return ResolvePackageIndex(asset, objProp2.Value);
                    }
                }
                return null;
            }
        
            // --- Build indices ---
            // Map export index (1-based) → K2Node export
            var k2Nodes = new Dictionary<int, NormalExport>();
            // Map export index → EdGraph export
            var edGraphs = new Dictionary<int, string>();
            // Map PinId GUID → (export index, pin name) for connection resolution
            var pinGuidMap = new Dictionary<Guid, (int exportIndex, string pinName)>();
        
            for (int i = 0; i < asset.Exports.Count; i++)
            {
                var export = asset.Exports[i] as NormalExport;
                if (export == null) continue;
        
                var classType = export.GetExportClassType()?.ToString() ?? "";
                if (classType.StartsWith("K2Node_") || classType == "K2Node")
                    k2Nodes[i + 1] = export;
                else if (classType == "EdGraph")
                    edGraphs[i + 1] = export.ObjectName.ToString();
            }
        
            // Group K2Nodes by parent EdGraph
            var graphNodeGroups = new Dictionary<string, List<int>>(); // graph name → list of export indices
            foreach (var (idx, node) in k2Nodes)
            {
                int outerIdx = node.OuterIndex?.Index ?? 0;
                string graphName = edGraphs.TryGetValue(outerIdx, out var name) ? name : $"Graph_{outerIdx}";
                if (!graphNodeGroups.ContainsKey(graphName))
                    graphNodeGroups[graphName] = new List<int>();
                graphNodeGroups[graphName].Add(idx);
            }
        
            // --- Parse pins for all K2Nodes ---
            // Stores parsed pin data per export index
            var nodePins = new Dictionary<int, List<ParsedPin>>();
            var parseErrors = new List<object>();
        
            foreach (var (idx, node) in k2Nodes)
            {
                var extras = node.Extras;
                if (extras == null || extras.Length < 4)
                {
                    nodePins[idx] = new List<ParsedPin>();
                    continue;
                }
        
                try
                {
                    using var ms = new MemoryStream(extras);
                    using var reader = new BinaryReader(ms);
        
                    int pinCount = reader.ReadInt32();
                    if (pinCount < 0 || pinCount > 500)
                    {
                        parseErrors.Add(new { export_index = idx, error = $"Bad pin count: {pinCount}" });
                        nodePins[idx] = new List<ParsedPin>();
                        continue;
                    }
        
                    var pins = new List<ParsedPin>();
                    for (int p = 0; p < pinCount; p++)
                    {
                        var pin = ReadOnePin(reader, asset, nameMap);
                        pins.Add(pin);
                        // Register in GUID map for connection resolution
                        pinGuidMap[pin.PinId] = (idx, pin.Name);
                    }
                    nodePins[idx] = pins;
                }
                catch (Exception ex)
                {
                    parseErrors.Add(new
                    {
                        export_index = idx,
                        class_type = node.GetExportClassType()?.ToString(),
                        error = ex.Message
                    });
                    nodePins[idx] = new List<ParsedPin>();
                }
            }
        
            // --- Identify Knot and inlineable nodes for graph compaction ---
            var knotNodeIds = new HashSet<int>();
            var inlineNodeIds = new HashSet<int>();
            // Maps (exportIndex, pinName) → compact inline string
            var inlineMap = new Dictionary<(int, string), string>();
        
            foreach (var (idx, node) in k2Nodes)
            {
                var classType = node.GetExportClassType()?.ToString() ?? "";
        
                if (classType == "K2Node_Knot")
                {
                    knotNodeIds.Add(idx);
                }
                else if (classType == "K2Node_Self")
                {
                    inlineNodeIds.Add(idx);
                    if (nodePins.TryGetValue(idx, out var selfPins))
                    {
                        foreach (var pin in selfPins)
                        {
                            if (pin.Direction == "out")
                                inlineMap[(idx, pin.Name)] = "self";
                        }
                    }
                }
                else if (classType == "K2Node_VariableGet")
                {
                    if (nodePins.TryGetValue(idx, out var vgPins))
                    {
                        var outPins = vgPins.Where(p => p.Direction == "out").ToList();
                        if (outPins.Count <= 2)
                        {
                            var varName = ResolveNodeTarget(node, classType) ?? "Unknown";
                            inlineNodeIds.Add(idx);
                            foreach (var pin in outPins)
                                inlineMap[(idx, pin.Name)] = $"var:{varName}";
                        }
                    }
                }
            }
        
            // Resolve Knot pass-throughs: follow chains of Knots to all real targets (handles fan-out)
            List<(int exportIndex, string pinName)> ResolveKnotTargets(int exportIdx, string pinName, HashSet<(int, Guid)>? visited = null)
            {
                if (!knotNodeIds.Contains(exportIdx))
                    return new List<(int, string)> { (exportIdx, pinName) };
                if (!nodePins.TryGetValue(exportIdx, out var knotPins))
                    return new List<(int, string)>();
        
                // Find the pin we arrived at
                var arrivedPin = knotPins.FirstOrDefault(p => p.Name == pinName);
                if (arrivedPin.Name == null) return new List<(int, string)>();
        
                // Follow through to the OTHER direction pin (in→out, out→in)
                var otherDir = arrivedPin.Direction == "in" ? "out" : "in";
                var otherPin = knotPins.FirstOrDefault(p => p.Direction == otherDir);
                if (otherPin.Name == null || otherPin.LinkedTo.Count == 0) return new List<(int, string)>();
        
                visited ??= new HashSet<(int, Guid)>();
                var results = new List<(int, string)>();
                foreach (var (nextNodeRef, nextPinGuid) in otherPin.LinkedTo)
                {
                    if (!visited.Add((nextNodeRef, nextPinGuid))) continue; // cycle
                    if (pinGuidMap.TryGetValue(nextPinGuid, out var next))
                        results.AddRange(ResolveKnotTargets(next.exportIndex, next.pinName, visited));
                }
                return results;
            }
        
            // Asset name
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);
            var bpName = bpExport?.ObjectName.ToString()
                ?? Path.GetFileNameWithoutExtension(ProgramContext.assetPath);
        
            var functions = new List<GraphFunctionData>();
        
            foreach (var (graphName, nodeIndices) in graphNodeGroups)
            {
                var functionNodes = new List<GraphNodeData>();
        
                foreach (var nodeIdx in nodeIndices)
                {
                    if (!k2Nodes.TryGetValue(nodeIdx, out var node)) continue;
                    if (!nodePins.TryGetValue(nodeIdx, out var pins)) continue;
                    if (knotNodeIds.Contains(nodeIdx) || inlineNodeIds.Contains(nodeIdx)) continue;
        
                    var classType = node.GetExportClassType()?.ToString() ?? "";
                    var shortType = classType.StartsWith("K2Node_") ? classType.Substring(7) : classType;
                    var target = ResolveNodeTarget(node, classType);
        
                    // Early-exit: skip nodes with zero connections and no meaningful pins
                    bool hasAnyConnection = pins.Any(p => p.LinkedTo.Count > 0);
                    if (!hasAnyConnection) continue;
        
                    var nodePinsList = new List<GraphPinData>();
        
                    foreach (var pin in pins)
                    {
                        // Skip hidden and orphaned pins
                        if (pin.IsHidden || pin.IsOrphaned) continue;
        
                        // Skip self input pins with no connections (noise)
                        if (pin.Name == "self" && pin.Direction == "in" && pin.LinkedTo.Count == 0)
                            continue;
        
                        // Skip unconnected pins with no user-set default (just node shape declarations)
                        if (pin.LinkedTo.Count == 0 && string.IsNullOrWhiteSpace(pin.DefaultValue))
                            continue;
        
                        var pinData = new GraphPinData
                        {
                            Name = pin.Name,
                            Dir = pin.Direction,
                            Cat = pin.Category,
                        };
        
                        if (!string.IsNullOrEmpty(pin.SubCategoryObject))
                            pinData.Sub = pin.SubCategoryObject;
                        if (pin.ContainerType == 1) pinData.Container = "array";
                        else if (pin.ContainerType == 2) pinData.Container = "set";
                        else if (pin.ContainerType == 3) pinData.Container = "map";
                        if (!string.IsNullOrEmpty(pin.DefaultValue))
                            pinData.Default = pin.DefaultValue;
        
                        // Resolve connections: follow through Knots, substitute inline refs
                        if (pin.LinkedTo.Count > 0)
                        {
                            var targets = new List<string>();
                            foreach (var (linkedNodeRef, linkedPinGuid) in pin.LinkedTo)
                            {
                                if (!pinGuidMap.TryGetValue(linkedPinGuid, out var resolved))
                                {
                                    targets.Add($"{linkedNodeRef}:{linkedPinGuid}");
                                    continue;
                                }
        
                                // Follow through Knot nodes to find all real targets (handles fan-out)
                                var finals = ResolveKnotTargets(resolved.exportIndex, resolved.pinName);
                                foreach (var (finalIdx, finalPin) in finals)
                                {
                                    // Substitute inline references for VariableGet/Self nodes
                                    if (inlineMap.TryGetValue((finalIdx, finalPin), out var inlineRef))
                                        targets.Add(inlineRef);
                                    else
                                        targets.Add($"{finalIdx}:{finalPin}");
                                }
                            }
                            if (targets.Count > 0)
                                pinData.To = targets;
                        }
        
                        nodePinsList.Add(pinData);
                    }
        
                    functionNodes.Add(new GraphNodeData
                    {
                        Id = nodeIdx,
                        Type = shortType,
                        Target = target,
                        Pins = nodePinsList,
                    });
                }
        
                if (functionNodes.Count > 0)
                {
                    functions.Add(new GraphFunctionData
                    {
                        Name = graphName,
                        Nodes = functionNodes,
                    });
                }
            }
        
            var graphData = new GraphData
            {
                Name = bpName,
                Functions = functions,
                Errors = parseErrors.Count > 0 ? parseErrors : null,
            };
        
            if (outputFormat == "json")
            {
                var options = new JsonSerializerOptions
                {
                    WriteIndented = true,
                    DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
                    PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                };
                Console.Write(JsonSerializer.Serialize(graphData, options));
                return;
            }
        
            // XML output
            var xml = new System.Text.StringBuilder();
            xml.AppendLine("<graph>");
            xml.AppendLine($"  <name>{EscapeXml(bpName)}</name>");
        
            foreach (var function in functions)
            {
                xml.AppendLine($"  <function name=\"{EscapeXml(function.Name)}\">");
                foreach (var node in function.Nodes)
                {
                    var targetAttr = !string.IsNullOrEmpty(node.Target) ? $" target=\"{EscapeXml(node.Target)}\"" : "";
                    xml.AppendLine($"    <node id=\"{node.Id}\" type=\"{EscapeXml(node.Type)}\"{targetAttr}>");
        
                    foreach (var pin in node.Pins)
                    {
                        var attrs = new System.Text.StringBuilder();
                        attrs.Append($" name=\"{EscapeXml(pin.Name)}\" dir=\"{pin.Dir}\" cat=\"{EscapeXml(pin.Cat)}\"");
        
                        if (!string.IsNullOrEmpty(pin.Sub))
                            attrs.Append($" sub=\"{EscapeXml(pin.Sub)}\"");
                        if (!string.IsNullOrEmpty(pin.Container))
                            attrs.Append($" container=\"{pin.Container}\"");
                        if (!string.IsNullOrEmpty(pin.Default))
                            attrs.Append($" default=\"{EscapeXml(pin.Default)}\"");
                        if (pin.To != null && pin.To.Count > 0)
                            attrs.Append($" to=\"{EscapeXml(string.Join(",", pin.To))}\"");
        
                        xml.AppendLine($"      <pin{attrs}/>");
                    }
        
                    xml.AppendLine("    </node>");
                }
                xml.AppendLine("  </function>");
            }
        
            // Parse errors as XML comments
            if (parseErrors.Count > 0)
            {
                foreach (var err in parseErrors)
                {
                    var errStr = JsonSerializer.Serialize(err);
                    xml.AppendLine($"  <!-- error: {EscapeXml(errStr)} -->");
                }
            }
        
            xml.AppendLine("</graph>");
            Console.Write(xml.ToString());
        }
        
        
    }
}

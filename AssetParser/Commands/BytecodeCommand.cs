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
    public static class BytecodeCommand
    {
        
        public static void ExtractBytecode(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
            xml.AppendLine("<bytecode>");
        
            // Asset name
            var bpExport = asset.Exports
                .OfType<NormalExport>()
                .FirstOrDefault(e => e.GetExportClassType()?.ToString()?.Contains("Blueprint") == true);
            var bpName = bpExport?.ObjectName.ToString()
                ?? Path.GetFileNameWithoutExtension(ProgramContext.assetPath);
            xml.AppendLine($"  <name>{EscapeXml(bpName)}</name>");
        
            foreach (var funcExport in asset.Exports.OfType<FunctionExport>())
            {
                var funcName = funcExport.ObjectName.ToString();
        
                // Skip truly internal functions but keep ExecuteUbergraph (event graph logic)
                if (funcName.StartsWith("bpv__")) continue;
                if (funcName.StartsWith("__")) continue;
                if (funcName.Contains("__TRASHFUNC")) continue;
                if (funcName.Contains("__TRASHEVENT")) continue;
                if (funcName.StartsWith("InpActEvt_")) continue;
                if (funcName.StartsWith("InpAxisEvt_")) continue;
                if (funcName.StartsWith("InpAxisKeyEvt_")) continue;
                if (funcName.StartsWith("InpTchEvt_")) continue;
        
                if (funcExport.ScriptBytecode == null || funcExport.ScriptBytecode.Length == 0)
                    continue;
        
                // Build CFG
                var cfg = BuildCFG(asset, funcExport);
                if (cfg.Blocks.Count == 0) continue;
        
                // Function flags
                var flags = funcExport.FunctionFlags.ToString();
                var simpleFlags = new List<string>();
                if (flags.Contains("BlueprintCallable")) simpleFlags.Add("Callable");
                if (flags.Contains("BlueprintPure")) simpleFlags.Add("Pure");
                if (flags.Contains("BlueprintEvent")) simpleFlags.Add("Event");
                if (flags.Contains("Native")) simpleFlags.Add("Native");
                var flagsAttr = simpleFlags.Count > 0 ? $" flags=\"{string.Join(",", simpleFlags)}\"" : "";
        
                xml.AppendLine($"  <function name=\"{EscapeXml(funcName)}\"{flagsAttr}>");
        
                // Parameters
                if (funcExport.LoadedProperties != null)
                {
                    var hasParams = false;
                    foreach (var prop in funcExport.LoadedProperties)
                    {
                        if (!prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_Parm)) continue;
                        if (!hasParams) { xml.AppendLine("    <params>"); hasParams = true; }
                        var paramName = prop.Name?.ToString() ?? "?";
                        var paramType = (prop.SerializedType?.ToString() ?? "?").Replace("Property", "");
                        string dir = prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReturnParm) ? "return" :
                                     prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_OutParm) &&
                                     !prop.PropertyFlags.HasFlag(EPropertyFlags.CPF_ReferenceParm) ? "out" : "in";
                        xml.AppendLine($"      <param name=\"{EscapeXml(paramName)}\" type=\"{EscapeXml(paramType)}\" direction=\"{dir}\"/>");
                    }
                    if (hasParams) xml.AppendLine("    </params>");
                }
        
                // Emit basic blocks
                foreach (var block in cfg.Blocks)
                {
                    xml.Append($"    <block id=\"{block.Id}\" offset=\"{block.StartOffset}\"");
                    if (block.Successors.Count > 0)
                        xml.Append($" successors=\"{string.Join(",", block.Successors)}\"");
                    if (block.IsLoopTarget)
                        xml.Append(" loop-target=\"true\"");
                    xml.AppendLine(">");
        
                    foreach (var instrIdx in block.Instructions)
                    {
                        var expr = funcExport.ScriptBytecode[instrIdx];
                        var pseudo = ExprToString(asset, expr, cfg.OffsetToBlock);
                        if (!string.IsNullOrEmpty(pseudo))
                            xml.AppendLine($"      <stmt>{EscapeXml(pseudo)}</stmt>");
                    }
        
                    xml.AppendLine("    </block>");
                }
        
                xml.AppendLine("  </function>");
            }
        
            xml.AppendLine("</bytecode>");
            Console.WriteLine(xml.ToString());
        }
        
        // Build a control flow graph for a single function's bytecode
        public static CFGResult BuildCFG(UAsset asset, FunctionExport funcExport)
        {
            var bytecode = funcExport.ScriptBytecode;
            var result = new CFGResult();
            if (bytecode == null || bytecode.Length == 0) return result;
        
            // Step 1: Build offset → index map by walking bytecode sizes
            var offsets = new List<uint>(); // offsets[i] = byte offset of instruction i
            uint currentOffset = 0;
            for (int i = 0; i < bytecode.Length; i++)
            {
                offsets.Add(currentOffset);
                currentOffset += bytecode[i].GetSize(asset);
            }
        
            // Step 2: Collect jump targets (these start new basic blocks)
            var blockStarts = new HashSet<uint> { 0 }; // Entry point is always a block start
            var jumpSources = new HashSet<int>();       // Indices of branching/terminating instructions
            var popTargets = new Dictionary<int, uint>(); // Best-effort target for pop-resume ops
        
            for (int i = 0; i < bytecode.Length; i++)
            {
                var expr = bytecode[i];
                switch (expr)
                {
                    case EX_Jump jump:
                        blockStarts.Add(jump.CodeOffset);
                        jumpSources.Add(i);
                        break;
                    case EX_JumpIfNot jumpIf:
                        blockStarts.Add(jumpIf.CodeOffset);
                        jumpSources.Add(i);
                        break;
                    case EX_PushExecutionFlow push:
                        blockStarts.Add(push.PushingAddress);
                        break;
                    case EX_SwitchValue sw:
                        blockStarts.Add(sw.EndGotoOffset);
                        if (sw.Cases != null)
                            foreach (var c in sw.Cases)
                                blockStarts.Add(c.NextOffset);
                        jumpSources.Add(i);
                        break;
                    case EX_PopExecutionFlow:
                    case EX_PopExecutionFlowIfNot:
                    case EX_Return:
                    case EX_EndOfScript:
                        jumpSources.Add(i);
                        break;
                    case EX_ComputedJump:
                        jumpSources.Add(i);
                        break;
                }
            }
        
            // Track flow-stack pushes to approximate pop targets for CFG edges.
            var flowStack = new Stack<uint>();
            for (int i = 0; i < bytecode.Length; i++)
            {
                var expr = bytecode[i];
                switch (expr)
                {
                    case EX_PushExecutionFlow push:
                        flowStack.Push(push.PushingAddress);
                        break;
                    case EX_PopExecutionFlow:
                        if (flowStack.Count > 0)
                            popTargets[i] = flowStack.Pop();
                        break;
                    case EX_PopExecutionFlowIfNot:
                        if (flowStack.Count > 0)
                            popTargets[i] = flowStack.Peek();
                        break;
                }
            }
        
            // Instructions after jump sources also start new blocks (fall-through targets)
            foreach (var srcIdx in jumpSources)
            {
                if (srcIdx + 1 < bytecode.Length)
                    blockStarts.Add(offsets[srcIdx + 1]);
            }
        
            // Step 3: Build basic blocks
            var offsetToIndex = new Dictionary<uint, int>();
            for (int i = 0; i < offsets.Count; i++)
                offsetToIndex[offsets[i]] = i;
        
            var sortedStarts = blockStarts.Where(s => offsetToIndex.ContainsKey(s)).OrderBy(s => s).ToList();
        
            foreach (var start in sortedStarts)
            {
                var block = new CFGBlock { StartOffset = start, Id = result.Blocks.Count };
                result.OffsetToBlock[start] = block.Id;
        
                int startIdx = offsetToIndex[start];
                for (int i = startIdx; i < bytecode.Length; i++)
                {
                    block.Instructions.Add(i);
        
                    // End block if this instruction branches/terminates
                    if (jumpSources.Contains(i))
                        break;
        
                    // End block if next instruction starts a new block
                    if (i + 1 < bytecode.Length && blockStarts.Contains(offsets[i + 1]))
                        break;
                }
        
                result.Blocks.Add(block);
            }
        
            // Step 4: Build edges (successor relationships)
            foreach (var block in result.Blocks)
            {
                if (block.Instructions.Count == 0) continue;
                int lastIdx = block.Instructions[^1];
                var lastExpr = bytecode[lastIdx];
                uint nextOffset = offsets[lastIdx] + lastExpr.GetSize(asset);
        
                switch (lastExpr)
                {
                    case EX_Jump jump:
                        if (result.OffsetToBlock.TryGetValue(jump.CodeOffset, out var jTarget))
                            block.Successors.Add(jTarget);
                        break;
        
                    case EX_JumpIfNot jumpIf:
                        // False branch (jump target)
                        if (result.OffsetToBlock.TryGetValue(jumpIf.CodeOffset, out var jifTarget))
                            block.Successors.Add(jifTarget);
                        // True branch (fall-through)
                        if (result.OffsetToBlock.TryGetValue(nextOffset, out var fallThrough))
                            block.Successors.Add(fallThrough);
                        break;
        
                    case EX_SwitchValue sw:
                        if (sw.Cases != null)
                            foreach (var c in sw.Cases)
                                if (result.OffsetToBlock.TryGetValue(c.NextOffset, out var caseTarget))
                                    block.Successors.Add(caseTarget);
                        if (result.OffsetToBlock.TryGetValue(sw.EndGotoOffset, out var defaultTarget))
                            block.Successors.Add(defaultTarget);
                        break;
        
                    case EX_Return:
                    case EX_EndOfScript:
                    case EX_ComputedJump:
                        // Terminal — no static successors
                        break;
        
                    case EX_PopExecutionFlow:
                        if (popTargets.TryGetValue(lastIdx, out var popTarget)
                            && result.OffsetToBlock.TryGetValue(popTarget, out var popBlock))
                            block.Successors.Add(popBlock);
                        break;
        
                    case EX_PopExecutionFlowIfNot:
                        // Conditional: if false → pop target, if true → fall-through
                        if (popTargets.TryGetValue(lastIdx, out var popIfTarget)
                            && result.OffsetToBlock.TryGetValue(popIfTarget, out var popIfBlock))
                            block.Successors.Add(popIfBlock);
                        if (result.OffsetToBlock.TryGetValue(nextOffset, out var popFt))
                            block.Successors.Add(popFt);
                        break;
        
                    default:
                        // Fall-through to next block
                        if (result.OffsetToBlock.TryGetValue(nextOffset, out var ft))
                            block.Successors.Add(ft);
                        break;
                }
            }
        
            // Step 5: Detect loop targets (blocks targeted by back-edges)
            foreach (var block in result.Blocks)
            {
                foreach (var succId in block.Successors)
                {
                    if (succId <= block.Id)
                        result.Blocks[succId].IsLoopTarget = true;
                }
            }
        
            return result;
        }
        
        // Convert a KismetExpression to a pseudocode string
        public static string ExprToString(UAsset asset, KismetExpression expr, Dictionary<uint, int> offsetToBlock)
        {
            if (expr == null) return "";
        
            switch (expr)
            {
                // === FUNCTION CALLS ===
                // Subclass order matters: LocalFinalFunction and CallMath extend FinalFunction
                case EX_LocalFinalFunction lff:
                    return $"{ResolvePackageIndex(asset, lff.StackNode)}({ParamsToString(asset, lff.Parameters, offsetToBlock)})";
                case EX_CallMath cm:
                    return $"{ResolvePackageIndex(asset, cm.StackNode)}({ParamsToString(asset, cm.Parameters, offsetToBlock)})";
                case EX_FinalFunction ff:
                    return $"{ResolvePackageIndex(asset, ff.StackNode)}({ParamsToString(asset, ff.Parameters, offsetToBlock)})";
                case EX_LocalVirtualFunction lvf:
                    return $"{lvf.VirtualFunctionName}({ParamsToString(asset, lvf.Parameters, offsetToBlock)})";
                case EX_VirtualFunction vf:
                    return $"{vf.VirtualFunctionName}({ParamsToString(asset, vf.Parameters, offsetToBlock)})";
        
                // === VARIABLE ACCESS ===
                case EX_InstanceVariable iv:
                    return ResolvePropertyPointer(asset, iv.Variable);
                case EX_LocalVariable lv:
                    return ResolvePropertyPointer(asset, lv.Variable);
                case EX_LocalOutVariable lov:
                    return ResolvePropertyPointer(asset, lov.Variable);
                case EX_DefaultVariable dv:
                    return ResolvePropertyPointer(asset, dv.Variable);
        
                // === ASSIGNMENTS ===
                // LetBase subclasses (LetObj, LetBool, etc.) must come before EX_Let
                case EX_LetObj lo:
                    return $"{ExprToString(asset, lo.VariableExpression, offsetToBlock)} = {ExprToString(asset, lo.AssignmentExpression, offsetToBlock)}";
                case EX_LetBool lb:
                    return $"{ExprToString(asset, lb.VariableExpression, offsetToBlock)} = {ExprToString(asset, lb.AssignmentExpression, offsetToBlock)}";
                case EX_LetDelegate ld:
                    return $"{ExprToString(asset, ld.VariableExpression, offsetToBlock)} = {ExprToString(asset, ld.AssignmentExpression, offsetToBlock)}";
                case EX_LetMulticastDelegate lmd:
                    return $"{ExprToString(asset, lmd.VariableExpression, offsetToBlock)} = {ExprToString(asset, lmd.AssignmentExpression, offsetToBlock)}";
                case EX_Let letExpr:
                    return $"{ExprToString(asset, letExpr.Variable, offsetToBlock)} = {ExprToString(asset, letExpr.Expression, offsetToBlock)}";
        
                // === CONTROL FLOW ===
                case EX_Jump j:
                {
                    var target = offsetToBlock.TryGetValue(j.CodeOffset, out var jb) ? $"block_{jb}" : $"@{j.CodeOffset}";
                    return $"goto {target}";
                }
                case EX_JumpIfNot jin:
                {
                    var target = offsetToBlock.TryGetValue(jin.CodeOffset, out var jinb) ? $"block_{jinb}" : $"@{jin.CodeOffset}";
                    return $"if not ({ExprToString(asset, jin.BooleanExpression, offsetToBlock)}) goto {target}";
                }
                case EX_Return ret:
                {
                    var retVal = ExprToString(asset, ret.ReturnExpression, offsetToBlock);
                    return string.IsNullOrEmpty(retVal) || retVal == "nothing" ? "return" : $"return {retVal}";
                }
                case EX_EndOfScript:
                    return ""; // Not meaningful for pseudocode
                case EX_PushExecutionFlow pef:
                {
                    var target = offsetToBlock.TryGetValue(pef.PushingAddress, out var pefb) ? $"block_{pefb}" : $"@{pef.PushingAddress}";
                    return $"push_resume {target}";
                }
                case EX_PopExecutionFlow:
                    return "pop_resume";
                case EX_PopExecutionFlowIfNot popIfNot:
                    return $"if not ({ExprToString(asset, popIfNot.BooleanExpression, offsetToBlock)}) pop_resume";
                case EX_SwitchValue sv:
                {
                    var indexStr = ExprToString(asset, sv.IndexTerm, offsetToBlock);
                    var caseStrs = sv.Cases?.Select(c =>
                        $"{ExprToString(asset, c.CaseIndexValueTerm, offsetToBlock)}: {ExprToString(asset, c.CaseTerm, offsetToBlock)}"
                    ) ?? Enumerable.Empty<string>();
                    var defaultStr = ExprToString(asset, sv.DefaultTerm, offsetToBlock);
                    return $"switch ({indexStr}) {{ {string.Join("; ", caseStrs)}; default: {defaultStr} }}";
                }
                case EX_ComputedJump cj:
                    return $"goto computed({ExprToString(asset, cj.CodeOffsetExpression, offsetToBlock)})";
        
                // === CONTEXT ===
                // Context_FailSilent extends Context, so subclass first
                case EX_Context_FailSilent cfs:
                {
                    var obj = ExprToString(asset, cfs.ObjectExpression, offsetToBlock);
                    var ctx = ExprToString(asset, cfs.ContextExpression, offsetToBlock);
                    return $"{obj}?.{ctx}";
                }
                case EX_Context ctxExpr:
                {
                    var obj = ExprToString(asset, ctxExpr.ObjectExpression, offsetToBlock);
                    var ctx = ExprToString(asset, ctxExpr.ContextExpression, offsetToBlock);
                    return $"{obj}.{ctx}";
                }
        
                // === CASTS ===
                case EX_DynamicCast dc:
                    return $"Cast<{ResolvePackageIndex(asset, dc.ClassPtr)}>({ExprToString(asset, dc.Target, offsetToBlock)})";
                case EX_MetaCast mc:
                    return $"ClassCast<{ResolvePackageIndex(asset, mc.ClassPtr)}>({ExprToString(asset, mc.Target, offsetToBlock)})";
        
                // === STRUCT MEMBER ===
                case EX_StructMemberContext smc:
                    return $"{ExprToString(asset, smc.StructExpression, offsetToBlock)}.{ResolvePropertyPointer(asset, smc.StructMemberExpression)}";
        
                // === ARRAY ===
                case EX_ArrayGetByRef agbr:
                    return $"{ExprToString(asset, agbr.ArrayVariable, offsetToBlock)}[{ExprToString(asset, agbr.ArrayIndex, offsetToBlock)}]";
                case EX_SetArray sa:
                {
                    var elems = sa.Elements?.Select(e => ExprToString(asset, e, offsetToBlock)) ?? Enumerable.Empty<string>();
                    return $"{ExprToString(asset, sa.AssigningProperty, offsetToBlock)} = [{string.Join(", ", elems)}]";
                }
                case EX_SetSet ss:
                {
                    var elems = ss.Elements?.Select(e => ExprToString(asset, e, offsetToBlock)) ?? Enumerable.Empty<string>();
                    return $"{ExprToString(asset, ss.SetProperty, offsetToBlock)} = Set({string.Join(", ", elems)})";
                }
                case EX_SetMap sm:
                {
                    var elems = sm.Elements?.Select(e => ExprToString(asset, e, offsetToBlock)) ?? Enumerable.Empty<string>();
                    return $"{ExprToString(asset, sm.MapProperty, offsetToBlock)} = Map({string.Join(", ", elems)})";
                }
        
                // === DELEGATES ===
                case EX_BindDelegate bd:
                    return $"BindDelegate({bd.FunctionName}, {ExprToString(asset, bd.Delegate, offsetToBlock)}, {ExprToString(asset, bd.ObjectTerm, offsetToBlock)})";
                case EX_AddMulticastDelegate amd:
                    return $"{ExprToString(asset, amd.Delegate, offsetToBlock)} += {ExprToString(asset, amd.DelegateToAdd, offsetToBlock)}";
                case EX_RemoveMulticastDelegate rmd:
                    return $"{ExprToString(asset, rmd.Delegate, offsetToBlock)} -= {ExprToString(asset, rmd.DelegateToAdd, offsetToBlock)}";
                case EX_ClearMulticastDelegate cmd:
                    return $"{ExprToString(asset, cmd.DelegateToClear, offsetToBlock)}.Clear()";
                case EX_InstanceDelegate instDel:
                    return $"&{instDel.FunctionName}";
        
                // === CONSTANTS ===
                case EX_IntConst ic:
                    return ic.Value.ToString();
                case EX_FloatConst fc:
                    return fc.Value.ToString("G");
                case EX_DoubleConst dc2:
                    return dc2.Value.ToString("G");
                case EX_StringConst sc:
                    return $"\"{sc.Value}\"";
                case EX_NameConst nc:
                    return nc.Value?.ToString() ?? "None";
                case EX_ByteConst bc:
                    return bc.Value.ToString();
                case EX_Int64Const i64:
                    return i64.Value.ToString();
                case EX_UInt64Const u64:
                    return u64.Value.ToString();
                case EX_True:
                    return "true";
                case EX_False:
                    return "false";
                case EX_IntZero:
                    return "0";
                case EX_IntOne:
                    return "1";
                case EX_Self:
                    return "self";
                case EX_NoObject:
                    return "null";
                case EX_Nothing:
                    return "";
                case EX_ObjectConst oc:
                    return ResolvePackageIndex(asset, oc.Value);
                case EX_VectorConst vc:
                    return $"Vector({vc.Value.X:G}, {vc.Value.Y:G}, {vc.Value.Z:G})";
                case EX_RotationConst rc:
                    return $"Rotator({rc.Value.Pitch:G}, {rc.Value.Yaw:G}, {rc.Value.Roll:G})";
                case EX_TransformConst tc:
                    return "Transform(...)";
                case EX_TextConst txc:
                    return $"Text(\"{txc.Value}\")";
                case EX_SoftObjectConst soc:
                    return $"SoftRef({ExprToString(asset, soc.Value, offsetToBlock)})";
                case EX_FieldPathConst fpc:
                    return $"FieldPath({ExprToString(asset, fpc.Value, offsetToBlock)})";
                case EX_PropertyConst pc:
                    return ResolvePropertyPointer(asset, pc.Property);
                case EX_SkipOffsetConst soc2:
                    return soc2.Value.ToString();
        
                // === INTERFACE ===
                case EX_InterfaceContext ic2:
                    return ExprToString(asset, ic2.InterfaceValue, offsetToBlock);
        
                // === CASTS (continued) ===
                // EX_CastBase subclasses: ObjToInterfaceCast, InterfaceToObjCast, CrossInterfaceCast
                case EX_ObjToInterfaceCast oic:
                    return $"InterfaceCast<{ResolvePackageIndex(asset, oic.ClassPtr)}>({ExprToString(asset, oic.Target, offsetToBlock)})";
                case EX_InterfaceToObjCast itoc:
                    return $"ObjCast<{ResolvePackageIndex(asset, itoc.ClassPtr)}>({ExprToString(asset, itoc.Target, offsetToBlock)})";
                case EX_PrimitiveCast primCast:
                    return $"PrimitiveCast<{primCast.ConversionType}>({ExprToString(asset, primCast.Target, offsetToBlock)})";
        
                // === STRUCT CONST ===
                case EX_StructConst structConst:
                {
                    var typeName = ResolvePackageIndex(asset, structConst.Struct);
                    if (structConst.Value != null && structConst.Value.Length > 0)
                    {
                        var fields = structConst.Value
                            .Select(f => ExprToString(asset, f, offsetToBlock))
                            .Where(s => !string.IsNullOrEmpty(s));
                        return $"{typeName}({string.Join(", ", fields)})";
                    }
                    return $"{typeName}()";
                }
        
                // === MISC ===
                case EX_Assert ae:
                    return $"assert({ExprToString(asset, ae.AssertExpression, offsetToBlock)})";
                case EX_LetValueOnPersistentFrame lvpf:
                    return $"{ResolvePropertyPointer(asset, lvpf.DestinationProperty)} = {ExprToString(asset, lvpf.AssignmentExpression, offsetToBlock)}";
                case EX_InstrumentationEvent:
                case EX_Tracepoint:
                case EX_WireTracepoint:
                    return ""; // Editor debug markers, skip
        
                default:
                    return $"[{expr.Token}]";
            }
        }
        
        public static string ParamsToString(UAsset asset, KismetExpression[]? parameters, Dictionary<uint, int> offsetToBlock)
        {
            if (parameters == null || parameters.Length == 0) return "";
            return string.Join(", ", parameters.Select(p => ExprToString(asset, p, offsetToBlock)));
        }
        
        
    }
}

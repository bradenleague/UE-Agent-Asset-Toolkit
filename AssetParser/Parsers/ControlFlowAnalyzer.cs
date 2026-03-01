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

namespace AssetParser.Parsers
{
    public static class ControlFlowAnalyzer
    {
        public static object AnalyzeControlFlow(KismetExpression[]? bytecode)
        {
            if (bytecode == null || bytecode.Length == 0)
                return null;
        
            int branchCount = 0;
            int switchCount = 0;
            bool hasReturn = false;
        
            foreach (var expr in bytecode)
            {
                CountControlFlowExpressions(expr, ref branchCount, ref switchCount, ref hasReturn);
            }
        
            bool hasBranches = branchCount > 0 || switchCount > 0;
        
            // Determine complexity
            // Low: 0-2 branches, Medium: 3-5, High: 6+
            int totalBranches = branchCount + switchCount;
            string complexity = totalBranches switch
            {
                0 => "none",
                <= 2 => "low",
                <= 5 => "medium",
                _ => "high"
            };
        
            return new
            {
                has_branches = hasBranches,
                has_loops = false,  // Loop detection deferred - requires back-edge analysis
                branch_count = branchCount,
                switch_count = switchCount,
                complexity = complexity
            };
        }
        
        public static void CountControlFlowExpressions(KismetExpression expr, ref int branchCount, ref int switchCount, ref bool hasReturn)
        {
            if (expr == null) return;
        
            switch (expr)
            {
                // Conditional branches
                case EX_JumpIfNot jumpIfNot:
                    branchCount++;
                    if (jumpIfNot.BooleanExpression != null)
                        CountControlFlowExpressions(jumpIfNot.BooleanExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                // Switch statements
                case EX_SwitchValue switchVal:
                    switchCount++;
                    if (switchVal.IndexTerm != null)
                        CountControlFlowExpressions(switchVal.IndexTerm, ref branchCount, ref switchCount, ref hasReturn);
                    if (switchVal.DefaultTerm != null)
                        CountControlFlowExpressions(switchVal.DefaultTerm, ref branchCount, ref switchCount, ref hasReturn);
                    if (switchVal.Cases != null)
                    {
                        foreach (var c in switchVal.Cases)
                        {
                            if (c.CaseIndexValueTerm != null)
                                CountControlFlowExpressions(c.CaseIndexValueTerm, ref branchCount, ref switchCount, ref hasReturn);
                            if (c.CaseTerm != null)
                                CountControlFlowExpressions(c.CaseTerm, ref branchCount, ref switchCount, ref hasReturn);
                        }
                    }
                    break;
        
                // Return statements
                case EX_Return returnExpr:
                    hasReturn = true;
                    if (returnExpr.ReturnExpression != null)
                        CountControlFlowExpressions(returnExpr.ReturnExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                // Recurse into nested expressions
                // Note: EX_Context_FailSilent extends EX_Context, so subclass must come first
                case EX_Context_FailSilent contextFail:
                    if (contextFail.ContextExpression != null)
                        CountControlFlowExpressions(contextFail.ContextExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                case EX_Context context:
                    if (context.ContextExpression != null)
                        CountControlFlowExpressions(context.ContextExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                case EX_Let letExpr:
                    if (letExpr.Expression != null)
                        CountControlFlowExpressions(letExpr.Expression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                case EX_LetObj letObj:
                    if (letObj.AssignmentExpression != null)
                        CountControlFlowExpressions(letObj.AssignmentExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
        
                case EX_LetBool letBool:
                    if (letBool.AssignmentExpression != null)
                        CountControlFlowExpressions(letBool.AssignmentExpression, ref branchCount, ref switchCount, ref hasReturn);
                    break;
            }
        }
        
        public static string ResolvePackageIndex(UAsset asset, FPackageIndex index)
        {
            if (index == null || index.Index == 0) return "[null]";
        
            try
            {
                if (index.IsImport())
                {
                    var import = index.ToImport(asset);
                    return import?.ObjectName.ToString() ?? $"[import:{index.Index}]";
                }
                if (index.IsExport())
                {
                    var export = index.ToExport(asset);
                    return export?.ObjectName.ToString() ?? $"[export:{index.Index}]";
                }
            }
            catch
            {
                // Fall through to unknown
            }
        
            return $"[unknown:{index.Index}]";
        }
        
        public static string ResolvePropertyPointer(UAsset asset, KismetPropertyPointer? ptr)
        {
            if (ptr == null) return "[null]";
        
            try
            {
                // UE5+ uses FFieldPath (New property)
                if (ptr.New?.Path != null && ptr.New.Path.Length > 0)
                {
                    return string.Join(".", ptr.New.Path.Select(p => p.ToString()));
                }
        
                // UE4 uses FPackageIndex (Old property)
                if (ptr.Old != null && ptr.Old.Index != 0)
                {
                    return ResolvePackageIndex(asset, ptr.Old);
                }
            }
            catch
            {
                // Fall through to unknown
            }
        
            return "[unknown]";
        }
        
        
    }
}

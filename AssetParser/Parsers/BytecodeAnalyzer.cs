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
    public class DelegateBindingObservation
    {
        public string OwnerFunction { get; set; } = "";
        public string Operation { get; set; } = "";
        public string DelegateTarget { get; set; } = "";
        public string? BoundFunction { get; set; }
        public string? ObjectTerm { get; set; }
        public string Source { get; set; } = "bytecode";
        public string Confidence { get; set; } = "medium";
    }

    public static class BytecodeAnalyzer
    {
        public static void AnalyzeExpression(UAsset asset, KismetExpression expr,
            HashSet<string> calls, HashSet<string> variables, HashSet<string> casts)
        {
            if (expr == null) return;
        
            switch (expr)
            {
                // Function calls - subclasses must come before parent classes
                // EX_LocalFinalFunction and EX_CallMath extend EX_FinalFunction
                case EX_LocalFinalFunction localFunc:
                    calls.Add(ResolvePackageIndex(asset, localFunc.StackNode));
                    AnalyzeParameters(asset, localFunc.Parameters, calls, variables, casts);
                    break;
        
                case EX_CallMath mathFunc:
                    calls.Add(ResolvePackageIndex(asset, mathFunc.StackNode));
                    AnalyzeParameters(asset, mathFunc.Parameters, calls, variables, casts);
                    break;
        
                case EX_FinalFunction finalFunc:
                    calls.Add(ResolvePackageIndex(asset, finalFunc.StackNode));
                    AnalyzeParameters(asset, finalFunc.Parameters, calls, variables, casts);
                    break;
        
                // EX_LocalVirtualFunction extends EX_VirtualFunction
                case EX_LocalVirtualFunction localVirtFunc:
                    calls.Add(localVirtFunc.VirtualFunctionName.ToString());
                    AnalyzeParameters(asset, localVirtFunc.Parameters, calls, variables, casts);
                    break;
        
                case EX_VirtualFunction virtFunc:
                    calls.Add(virtFunc.VirtualFunctionName.ToString());
                    AnalyzeParameters(asset, virtFunc.Parameters, calls, variables, casts);
                    break;
        
                // Variable access
                case EX_InstanceVariable instVar:
                    variables.Add(ResolvePropertyPointer(asset, instVar.Variable));
                    break;
        
                case EX_LocalVariable localVar:
                    variables.Add(ResolvePropertyPointer(asset, localVar.Variable));
                    break;
        
                case EX_LocalOutVariable localOutVar:
                    variables.Add(ResolvePropertyPointer(asset, localOutVar.Variable));
                    break;
        
                case EX_DefaultVariable defaultVar:
                    variables.Add(ResolvePropertyPointer(asset, defaultVar.Variable));
                    break;
        
                // Delegates
                case EX_InstanceDelegate instDelegate:
                    calls.Add(instDelegate.FunctionName.ToString());
                    break;
        
                // Casts (EX_CastBase provides ClassPtr and Target)
                case EX_DynamicCast dynCast:
                    casts.Add(ResolvePackageIndex(asset, dynCast.ClassPtr));
                    if (dynCast.Target != null)
                        AnalyzeExpression(asset, dynCast.Target, calls, variables, casts);
                    break;
        
                case EX_MetaCast metaCast:
                    casts.Add(ResolvePackageIndex(asset, metaCast.ClassPtr));
                    if (metaCast.Target != null)
                        AnalyzeExpression(asset, metaCast.Target, calls, variables, casts);
                    break;
        
                // Context expressions (subclass first to avoid unreachable code)
                case EX_Context_FailSilent contextFail:
                    if (contextFail.ObjectExpression != null)
                        AnalyzeExpression(asset, contextFail.ObjectExpression, calls, variables, casts);
                    if (contextFail.ContextExpression != null)
                        AnalyzeExpression(asset, contextFail.ContextExpression, calls, variables, casts);
                    break;
        
                case EX_Context context:
                    if (context.ObjectExpression != null)
                        AnalyzeExpression(asset, context.ObjectExpression, calls, variables, casts);
                    if (context.ContextExpression != null)
                        AnalyzeExpression(asset, context.ContextExpression, calls, variables, casts);
                    break;
        
                // Let expressions (subclasses extend EX_LetBase with VariableExpression/AssignmentExpression)
                // EX_LetBase subclasses must come before EX_Let (which has different structure)
                case EX_LetObj letObj:
                    if (letObj.VariableExpression != null)
                        AnalyzeExpression(asset, letObj.VariableExpression, calls, variables, casts);
                    if (letObj.AssignmentExpression != null)
                        AnalyzeExpression(asset, letObj.AssignmentExpression, calls, variables, casts);
                    break;
        
                case EX_LetBool letBool:
                    if (letBool.VariableExpression != null)
                        AnalyzeExpression(asset, letBool.VariableExpression, calls, variables, casts);
                    if (letBool.AssignmentExpression != null)
                        AnalyzeExpression(asset, letBool.AssignmentExpression, calls, variables, casts);
                    break;
        
                case EX_LetDelegate letDelegate:
                    if (letDelegate.VariableExpression != null)
                        AnalyzeExpression(asset, letDelegate.VariableExpression, calls, variables, casts);
                    if (letDelegate.AssignmentExpression != null)
                        AnalyzeExpression(asset, letDelegate.AssignmentExpression, calls, variables, casts);
                    break;
        
                case EX_LetMulticastDelegate letMulti:
                    if (letMulti.VariableExpression != null)
                        AnalyzeExpression(asset, letMulti.VariableExpression, calls, variables, casts);
                    if (letMulti.AssignmentExpression != null)
                        AnalyzeExpression(asset, letMulti.AssignmentExpression, calls, variables, casts);
                    break;
        
                // EX_Let has different structure: Value (KismetPropertyPointer), Variable (expression), Expression (expression)
                case EX_Let letExpr:
                    if (letExpr.Value != null)
                        variables.Add(ResolvePropertyPointer(asset, letExpr.Value));
                    if (letExpr.Variable != null)
                        AnalyzeExpression(asset, letExpr.Variable, calls, variables, casts);
                    if (letExpr.Expression != null)
                        AnalyzeExpression(asset, letExpr.Expression, calls, variables, casts);
                    break;
        
                case EX_StructMemberContext structMember:
                    if (structMember.StructExpression != null)
                        AnalyzeExpression(asset, structMember.StructExpression, calls, variables, casts);
                    if (structMember.StructMemberExpression != null)
                        variables.Add(ResolvePropertyPointer(asset, structMember.StructMemberExpression));
                    break;
        
                case EX_ArrayGetByRef arrayGet:
                    if (arrayGet.ArrayVariable != null)
                        AnalyzeExpression(asset, arrayGet.ArrayVariable, calls, variables, casts);
                    if (arrayGet.ArrayIndex != null)
                        AnalyzeExpression(asset, arrayGet.ArrayIndex, calls, variables, casts);
                    break;
        
                case EX_Return returnExpr:
                    if (returnExpr.ReturnExpression != null)
                        AnalyzeExpression(asset, returnExpr.ReturnExpression, calls, variables, casts);
                    break;
        
                case EX_JumpIfNot jumpIfNot:
                    if (jumpIfNot.BooleanExpression != null)
                        AnalyzeExpression(asset, jumpIfNot.BooleanExpression, calls, variables, casts);
                    break;
        
                case EX_Assert assertExpr:
                    if (assertExpr.AssertExpression != null)
                        AnalyzeExpression(asset, assertExpr.AssertExpression, calls, variables, casts);
                    break;
        
                case EX_SetArray setArray:
                    if (setArray.AssigningProperty != null)
                        AnalyzeExpression(asset, setArray.AssigningProperty, calls, variables, casts);
                    if (setArray.Elements != null)
                    {
                        foreach (var elem in setArray.Elements)
                            AnalyzeExpression(asset, elem, calls, variables, casts);
                    }
                    break;
        
                case EX_SetSet setSet:
                    if (setSet.SetProperty != null)
                        AnalyzeExpression(asset, setSet.SetProperty, calls, variables, casts);
                    if (setSet.Elements != null)
                    {
                        foreach (var elem in setSet.Elements)
                            AnalyzeExpression(asset, elem, calls, variables, casts);
                    }
                    break;
        
                case EX_SetMap setMap:
                    if (setMap.MapProperty != null)
                        AnalyzeExpression(asset, setMap.MapProperty, calls, variables, casts);
                    if (setMap.Elements != null)
                    {
                        foreach (var elem in setMap.Elements)
                            AnalyzeExpression(asset, elem, calls, variables, casts);
                    }
                    break;
        
                case EX_SwitchValue switchVal:
                    if (switchVal.IndexTerm != null)
                        AnalyzeExpression(asset, switchVal.IndexTerm, calls, variables, casts);
                    if (switchVal.DefaultTerm != null)
                        AnalyzeExpression(asset, switchVal.DefaultTerm, calls, variables, casts);
                    if (switchVal.Cases != null)
                    {
                        foreach (var c in switchVal.Cases)
                        {
                            if (c.CaseIndexValueTerm != null)
                                AnalyzeExpression(asset, c.CaseIndexValueTerm, calls, variables, casts);
                            if (c.CaseTerm != null)
                                AnalyzeExpression(asset, c.CaseTerm, calls, variables, casts);
                        }
                    }
                    break;
        
                case EX_BindDelegate bindDelegate:
                    calls.Add(bindDelegate.FunctionName.ToString());
                    if (bindDelegate.Delegate != null)
                        AnalyzeExpression(asset, bindDelegate.Delegate, calls, variables, casts);
                    if (bindDelegate.ObjectTerm != null)
                        AnalyzeExpression(asset, bindDelegate.ObjectTerm, calls, variables, casts);
                    break;
        
                case EX_AddMulticastDelegate addMulti:
                    if (addMulti.Delegate != null)
                        AnalyzeExpression(asset, addMulti.Delegate, calls, variables, casts);
                    if (addMulti.DelegateToAdd != null)
                        AnalyzeExpression(asset, addMulti.DelegateToAdd, calls, variables, casts);
                    break;
        
                case EX_RemoveMulticastDelegate removeMulti:
                    if (removeMulti.Delegate != null)
                        AnalyzeExpression(asset, removeMulti.Delegate, calls, variables, casts);
                    if (removeMulti.DelegateToAdd != null)
                        AnalyzeExpression(asset, removeMulti.DelegateToAdd, calls, variables, casts);
                    break;
        
                case EX_ClearMulticastDelegate clearMulti:
                    if (clearMulti.DelegateToClear != null)
                        AnalyzeExpression(asset, clearMulti.DelegateToClear, calls, variables, casts);
                    break;
        
                case EX_InterfaceContext interfaceCtx:
                    if (interfaceCtx.InterfaceValue != null)
                        AnalyzeExpression(asset, interfaceCtx.InterfaceValue, calls, variables, casts);
                    break;
        
                case EX_ObjectConst objConst:
                    // Extract the constant object reference for context
                    var objName = ResolvePackageIndex(asset, objConst.Value);
                    if (!string.IsNullOrEmpty(objName) && objName != "[null]")
                        variables.Add(objName);
                    break;
        
                case EX_SoftObjectConst softObjConst:
                    if (softObjConst.Value != null)
                        AnalyzeExpression(asset, softObjConst.Value, calls, variables, casts);
                    break;
        
                case EX_FieldPathConst fieldPathConst:
                    if (fieldPathConst.Value != null)
                        AnalyzeExpression(asset, fieldPathConst.Value, calls, variables, casts);
                    break;
        
                case EX_PropertyConst propConst:
                    variables.Add(ResolvePropertyPointer(asset, propConst.Property));
                    break;
            }
        }
        
        public static void AnalyzeParameters(UAsset asset, KismetExpression[]? parameters,
            HashSet<string> calls, HashSet<string> variables, HashSet<string> casts)
        {
            if (parameters == null) return;
            foreach (var param in parameters)
            {
                AnalyzeExpression(asset, param, calls, variables, casts);
            }
        }

        public static List<DelegateBindingObservation> CollectDelegateBindings(UAsset asset, FunctionExport funcExport)
        {
            var observations = new List<DelegateBindingObservation>();
            if (funcExport.ScriptBytecode == null || funcExport.ScriptBytecode.Length == 0)
                return observations;

            var ownerFunction = funcExport.ObjectName.ToString();
            foreach (var expr in funcExport.ScriptBytecode)
            {
                AnalyzeDelegateBindingExpression(asset, expr, ownerFunction, observations);
            }
            return observations;
        }

        public static void AnalyzeDelegateBindingExpression(
            UAsset asset,
            KismetExpression? expr,
            string ownerFunction,
            List<DelegateBindingObservation> observations)
        {
            if (expr == null) return;

            switch (expr)
            {
                case EX_BindDelegate bindDelegate:
                    observations.Add(new DelegateBindingObservation
                    {
                        OwnerFunction = ownerFunction,
                        Operation = "bind_delegate",
                        DelegateTarget = DescribeDelegateExpression(asset, bindDelegate.Delegate),
                        BoundFunction = bindDelegate.FunctionName.ToString(),
                        ObjectTerm = DescribeDelegateExpression(asset, bindDelegate.ObjectTerm),
                        Confidence = string.IsNullOrWhiteSpace(bindDelegate.FunctionName.ToString()) ? "low" : "high",
                    });
                    break;

                case EX_AddMulticastDelegate addMulti:
                    observations.Add(new DelegateBindingObservation
                    {
                        OwnerFunction = ownerFunction,
                        Operation = "add_multicast",
                        DelegateTarget = DescribeDelegateExpression(asset, addMulti.Delegate),
                        BoundFunction = TryExtractBoundFunction(asset, addMulti.DelegateToAdd),
                        ObjectTerm = null,
                        Confidence = TryExtractBoundFunction(asset, addMulti.DelegateToAdd) != null ? "high" : "medium",
                    });
                    break;

                case EX_RemoveMulticastDelegate removeMulti:
                    observations.Add(new DelegateBindingObservation
                    {
                        OwnerFunction = ownerFunction,
                        Operation = "remove_multicast",
                        DelegateTarget = DescribeDelegateExpression(asset, removeMulti.Delegate),
                        BoundFunction = TryExtractBoundFunction(asset, removeMulti.DelegateToAdd),
                        ObjectTerm = null,
                        Confidence = TryExtractBoundFunction(asset, removeMulti.DelegateToAdd) != null ? "high" : "medium",
                    });
                    break;

                case EX_ClearMulticastDelegate clearMulti:
                    observations.Add(new DelegateBindingObservation
                    {
                        OwnerFunction = ownerFunction,
                        Operation = "clear_multicast",
                        DelegateTarget = DescribeDelegateExpression(asset, clearMulti.DelegateToClear),
                        BoundFunction = null,
                        ObjectTerm = null,
                        Confidence = "high",
                    });
                    break;
            }

            foreach (var child in EnumerateChildExpressions(expr))
            {
                AnalyzeDelegateBindingExpression(asset, child, ownerFunction, observations);
            }
        }

        public static IEnumerable<KismetExpression> EnumerateChildExpressions(KismetExpression expr)
        {
            switch (expr)
            {
                case EX_LocalFinalFunction localFunc when localFunc.Parameters != null:
                    foreach (var p in localFunc.Parameters) yield return p;
                    break;
                case EX_CallMath mathFunc when mathFunc.Parameters != null:
                    foreach (var p in mathFunc.Parameters) yield return p;
                    break;
                case EX_FinalFunction finalFunc when finalFunc.Parameters != null:
                    foreach (var p in finalFunc.Parameters) yield return p;
                    break;
                case EX_LocalVirtualFunction localVirtFunc when localVirtFunc.Parameters != null:
                    foreach (var p in localVirtFunc.Parameters) yield return p;
                    break;
                case EX_VirtualFunction virtFunc when virtFunc.Parameters != null:
                    foreach (var p in virtFunc.Parameters) yield return p;
                    break;
                case EX_Context_FailSilent contextFail:
                    if (contextFail.ObjectExpression != null) yield return contextFail.ObjectExpression;
                    if (contextFail.ContextExpression != null) yield return contextFail.ContextExpression;
                    break;
                case EX_Context context:
                    if (context.ObjectExpression != null) yield return context.ObjectExpression;
                    if (context.ContextExpression != null) yield return context.ContextExpression;
                    break;
                case EX_LetObj letObj:
                    if (letObj.VariableExpression != null) yield return letObj.VariableExpression;
                    if (letObj.AssignmentExpression != null) yield return letObj.AssignmentExpression;
                    break;
                case EX_LetBool letBool:
                    if (letBool.VariableExpression != null) yield return letBool.VariableExpression;
                    if (letBool.AssignmentExpression != null) yield return letBool.AssignmentExpression;
                    break;
                case EX_LetDelegate letDelegate:
                    if (letDelegate.VariableExpression != null) yield return letDelegate.VariableExpression;
                    if (letDelegate.AssignmentExpression != null) yield return letDelegate.AssignmentExpression;
                    break;
                case EX_LetMulticastDelegate letMulti:
                    if (letMulti.VariableExpression != null) yield return letMulti.VariableExpression;
                    if (letMulti.AssignmentExpression != null) yield return letMulti.AssignmentExpression;
                    break;
                case EX_Let letExpr:
                    if (letExpr.Variable != null) yield return letExpr.Variable;
                    if (letExpr.Expression != null) yield return letExpr.Expression;
                    break;
                case EX_StructMemberContext structMember:
                    if (structMember.StructExpression != null) yield return structMember.StructExpression;
                    break;
                case EX_ArrayGetByRef arrayGet:
                    if (arrayGet.ArrayVariable != null) yield return arrayGet.ArrayVariable;
                    if (arrayGet.ArrayIndex != null) yield return arrayGet.ArrayIndex;
                    break;
                case EX_Return returnExpr:
                    if (returnExpr.ReturnExpression != null) yield return returnExpr.ReturnExpression;
                    break;
                case EX_JumpIfNot jumpIfNot:
                    if (jumpIfNot.BooleanExpression != null) yield return jumpIfNot.BooleanExpression;
                    break;
                case EX_Assert assertExpr:
                    if (assertExpr.AssertExpression != null) yield return assertExpr.AssertExpression;
                    break;
                case EX_SetArray setArray:
                    if (setArray.AssigningProperty != null) yield return setArray.AssigningProperty;
                    if (setArray.Elements != null)
                        foreach (var elem in setArray.Elements) yield return elem;
                    break;
                case EX_SetSet setSet:
                    if (setSet.SetProperty != null) yield return setSet.SetProperty;
                    if (setSet.Elements != null)
                        foreach (var elem in setSet.Elements) yield return elem;
                    break;
                case EX_SetMap setMap:
                    if (setMap.MapProperty != null) yield return setMap.MapProperty;
                    if (setMap.Elements != null)
                        foreach (var elem in setMap.Elements) yield return elem;
                    break;
                case EX_SwitchValue switchVal:
                    if (switchVal.IndexTerm != null) yield return switchVal.IndexTerm;
                    if (switchVal.DefaultTerm != null) yield return switchVal.DefaultTerm;
                    if (switchVal.Cases != null)
                    {
                        foreach (var c in switchVal.Cases)
                        {
                            if (c.CaseIndexValueTerm != null) yield return c.CaseIndexValueTerm;
                            if (c.CaseTerm != null) yield return c.CaseTerm;
                        }
                    }
                    break;
                case EX_BindDelegate bindDelegate:
                    if (bindDelegate.Delegate != null) yield return bindDelegate.Delegate;
                    if (bindDelegate.ObjectTerm != null) yield return bindDelegate.ObjectTerm;
                    break;
                case EX_AddMulticastDelegate addMulti:
                    if (addMulti.Delegate != null) yield return addMulti.Delegate;
                    if (addMulti.DelegateToAdd != null) yield return addMulti.DelegateToAdd;
                    break;
                case EX_RemoveMulticastDelegate removeMulti:
                    if (removeMulti.Delegate != null) yield return removeMulti.Delegate;
                    if (removeMulti.DelegateToAdd != null) yield return removeMulti.DelegateToAdd;
                    break;
                case EX_ClearMulticastDelegate clearMulti:
                    if (clearMulti.DelegateToClear != null) yield return clearMulti.DelegateToClear;
                    break;
                case EX_DynamicCast dynCast:
                    if (dynCast.Target != null) yield return dynCast.Target;
                    break;
                case EX_MetaCast metaCast:
                    if (metaCast.Target != null) yield return metaCast.Target;
                    break;
                case EX_InterfaceContext interfaceCtx:
                    if (interfaceCtx.InterfaceValue != null) yield return interfaceCtx.InterfaceValue;
                    break;
                case EX_SoftObjectConst softObjConst:
                    if (softObjConst.Value != null) yield return softObjConst.Value;
                    break;
                case EX_FieldPathConst fieldPathConst:
                    if (fieldPathConst.Value != null) yield return fieldPathConst.Value;
                    break;
            }
        }

        public static string DescribeDelegateExpression(UAsset asset, KismetExpression? expr)
        {
            if (expr == null) return "[null]";

            return expr switch
            {
                EX_InstanceVariable instVar => ResolvePropertyPointer(asset, instVar.Variable),
                EX_LocalVariable localVar => ResolvePropertyPointer(asset, localVar.Variable),
                EX_DefaultVariable defaultVar => ResolvePropertyPointer(asset, defaultVar.Variable),
                EX_InstanceDelegate instDelegate => "&" + instDelegate.FunctionName.ToString(),
                EX_ObjectConst objConst => ResolvePackageIndex(asset, objConst.Value),
                EX_Self => "self",
                EX_Context_FailSilent contextFail => $"{DescribeDelegateExpression(asset, contextFail.ObjectExpression)}?.{DescribeDelegateExpression(asset, contextFail.ContextExpression)}",
                EX_LocalFinalFunction localFinalFunc => ResolvePackageIndex(asset, localFinalFunc.StackNode),
                EX_FinalFunction finalFunc => ResolvePackageIndex(asset, finalFunc.StackNode),
                EX_LocalVirtualFunction localVirtFunc => localVirtFunc.VirtualFunctionName.ToString(),
                EX_VirtualFunction virtFunc => virtFunc.VirtualFunctionName.ToString(),
                EX_Context context => $"{DescribeDelegateExpression(asset, context.ObjectExpression)}.{DescribeDelegateExpression(asset, context.ContextExpression)}",
                _ => $"[{expr.Token}]",
            };
        }

        public static string? TryExtractBoundFunction(UAsset asset, KismetExpression? expr)
        {
            if (expr == null) return null;

            return expr switch
            {
                EX_InstanceDelegate instDelegate => instDelegate.FunctionName.ToString(),
                EX_BindDelegate bindDelegate => bindDelegate.FunctionName.ToString(),
                EX_LocalVirtualFunction localVirtFunc => localVirtFunc.VirtualFunctionName.ToString(),
                EX_VirtualFunction virtFunc => virtFunc.VirtualFunctionName.ToString(),
                EX_LocalFinalFunction localFinalFunc => ResolvePackageIndex(asset, localFinalFunc.StackNode),
                EX_FinalFunction finalFunc => ResolvePackageIndex(asset, finalFunc.StackNode),
                _ => null,
            };
        }
        
        
    }
}

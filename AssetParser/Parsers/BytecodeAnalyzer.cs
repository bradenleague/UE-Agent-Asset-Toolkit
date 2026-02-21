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
        
        
    }
}

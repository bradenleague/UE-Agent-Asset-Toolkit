using AssetParser.Parsers;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using System.Linq;
using UAssetAPI;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.UnrealTypes;

namespace AssetParser.Tests;

[TestClass]
public class BytecodeAnalyzerTests
{
    [TestMethod]
    public void AnalyzeExpression_CollectsVirtualFunctionCallsAndParameterVariables()
    {
        var calls = new HashSet<string>();
        var variables = new HashSet<string>();
        var casts = new HashSet<string>();

        var expr = new EX_LocalVirtualFunction
        {
            VirtualFunctionName = FName.DefineDummy(null, "DoThing"),
            Parameters =
            [
                new EX_LocalVariable { Variable = null }
            ]
        };

        BytecodeAnalyzer.AnalyzeExpression(new UAsset(), expr, calls, variables, casts);

        Assert.IsTrue(calls.Contains("DoThing"));
        Assert.IsTrue(variables.Contains("[null]"));
        Assert.AreEqual(0, casts.Count);
    }

    [TestMethod]
    public void AnalyzeExpression_CollectsCastAndNestedCall()
    {
        var calls = new HashSet<string>();
        var variables = new HashSet<string>();
        var casts = new HashSet<string>();

        var expr = new EX_DynamicCast
        {
            ClassPtr = new FPackageIndex(0),
            Target = new EX_LocalVirtualFunction
            {
                VirtualFunctionName = FName.DefineDummy(null, "NestedCall"),
                Parameters = Array.Empty<KismetExpression>()
            }
        };

        BytecodeAnalyzer.AnalyzeExpression(new UAsset(), expr, calls, variables, casts);

        Assert.IsTrue(casts.Contains("[null]"));
        Assert.IsTrue(calls.Contains("NestedCall"));
    }

    [TestMethod]
    public void AnalyzeParameters_IsNoOp_WhenNull()
    {
        var calls = new HashSet<string>();
        var variables = new HashSet<string>();
        var casts = new HashSet<string>();

        BytecodeAnalyzer.AnalyzeParameters(new UAsset(), null, calls, variables, casts);

        Assert.AreEqual(0, calls.Count);
        Assert.AreEqual(0, variables.Count);
        Assert.AreEqual(0, casts.Count);
    }

    [TestMethod]
    public void CollectDelegateBindings_FindsBindAndMulticastOperations()
    {
        var function = new UAssetAPI.ExportTypes.FunctionExport
        {
            ObjectName = FName.DefineDummy(null, "SetupDelegates"),
            ScriptBytecode =
            [
                new EX_BindDelegate
                {
                    FunctionName = FName.DefineDummy(null, "OnBound"),
                    Delegate = new EX_InstanceVariable { Variable = null },
                    ObjectTerm = new EX_Self()
                },
                new EX_AddMulticastDelegate
                {
                    Delegate = new EX_InstanceVariable { Variable = null },
                    DelegateToAdd = new EX_InstanceDelegate
                    {
                        FunctionName = FName.DefineDummy(null, "OnMulti")
                    }
                }
            ]
        };

        var bindings = BytecodeAnalyzer.CollectDelegateBindings(new UAsset(), function);

        Assert.AreEqual(2, bindings.Count);
        Assert.IsTrue(bindings.Any(b => b.Operation == "bind_delegate" && b.BoundFunction == "OnBound"));
        Assert.IsTrue(bindings.Any(b => b.Operation == "add_multicast" && b.BoundFunction == "OnMulti"));
    }
}

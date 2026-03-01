using AssetParser.Parsers;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;

namespace AssetParser.Tests;

[TestClass]
public class ControlFlowAnalyzerTests
{
    [TestMethod]
    public void AnalyzeControlFlow_ReturnsNull_WhenNoBytecode()
    {
        Assert.IsNull(ControlFlowAnalyzer.AnalyzeControlFlow(null));
        Assert.IsNull(ControlFlowAnalyzer.AnalyzeControlFlow([]));
    }

    [TestMethod]
    public void AnalyzeControlFlow_CountsBranchesAndSwitches()
    {
        var bytecode = new KismetExpression[]
        {
            new EX_JumpIfNot
            {
                CodeOffset = 24,
                BooleanExpression = new EX_True()
            },
            new EX_SwitchValue
            {
                EndGotoOffset = 64,
                IndexTerm = new EX_IntConst { Value = 1 },
                Cases =
                [
                    new FKismetSwitchCase(
                        new EX_IntConst { Value = 2 },
                        32,
                        new EX_IntConst { Value = 3 })
                ],
                DefaultTerm = new EX_IntConst { Value = 0 }
            }
        };

        var result = ControlFlowAnalyzer.AnalyzeControlFlow(bytecode);

        Assert.IsNotNull(result);
        Assert.AreEqual(1, GetAnonymousProperty<int>(result!, "branch_count"));
        Assert.AreEqual(1, GetAnonymousProperty<int>(result!, "switch_count"));
        Assert.IsTrue(GetAnonymousProperty<bool>(result!, "has_branches"));
        Assert.AreEqual("low", GetAnonymousProperty<string>(result!, "complexity"));
    }

    [TestMethod]
    public void CountControlFlowExpressions_TracksNestedBranchesAndReturns()
    {
        var expr = new EX_Context
        {
            ContextExpression = new EX_LetBool
            {
                AssignmentExpression = new EX_Return
                {
                    ReturnExpression = new EX_JumpIfNot
                    {
                        CodeOffset = 18,
                        BooleanExpression = new EX_False()
                    }
                }
            }
        };

        int branchCount = 0;
        int switchCount = 0;
        bool hasReturn = false;

        ControlFlowAnalyzer.CountControlFlowExpressions(expr, ref branchCount, ref switchCount, ref hasReturn);

        Assert.AreEqual(1, branchCount);
        Assert.AreEqual(0, switchCount);
        Assert.IsTrue(hasReturn);
    }

    private static T GetAnonymousProperty<T>(object source, string name)
    {
        var prop = source.GetType().GetProperty(name);
        Assert.IsNotNull(prop, $"Missing property '{name}'");
        return (T)prop!.GetValue(source)!;
    }
}

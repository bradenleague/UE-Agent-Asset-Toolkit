using AssetParser.Commands;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;

namespace AssetParser.Tests;

[TestClass]
public class BytecodeCommandTests
{
    [TestMethod]
    public void ParamsToString_FormatsSimpleParameters()
    {
        var result = BytecodeCommand.ParamsToString(
            new UAsset(),
            new KismetExpression[]
            {
                new EX_IntConst { Value = 7 },
                new EX_True()
            },
            new Dictionary<uint, int>());

        Assert.AreEqual("7, true", result);
    }

    [TestMethod]
    public void ExprToString_UsesBlockNames_ForJumpIfNot()
    {
        var expr = new EX_JumpIfNot
        {
            CodeOffset = 42,
            BooleanExpression = new EX_False()
        };

        var result = BytecodeCommand.ExprToString(
            new UAsset(),
            expr,
            new Dictionary<uint, int> { [42] = 3 });

        Assert.AreEqual("if not (false) goto block_3", result);
    }

    [TestMethod]
    public void BuildCFG_CreatesBlocksAndBranchEdges()
    {
        var function = new FunctionExport
        {
            ScriptBytecode =
            [
                new EX_JumpIfNot
                {
                    // Offsets for this sequence are 0, 6, 12 based on GetSize().
                    CodeOffset = 12,
                    BooleanExpression = new EX_True()
                },
                new EX_Return { ReturnExpression = new EX_IntConst { Value = 1 } },
                new EX_Return { ReturnExpression = new EX_IntConst { Value = 0 } }
            ]
        };

        var cfg = BytecodeCommand.BuildCFG(new UAsset(), function);

        Assert.AreEqual(3, cfg.Blocks.Count);
        CollectionAssert.AreEquivalent(new[] { 1, 2 }, cfg.Blocks[0].Successors);
        Assert.AreEqual(6u, cfg.Blocks[1].StartOffset);
        Assert.AreEqual(12u, cfg.Blocks[2].StartOffset);
    }

    [TestMethod]
    public void BuildCFG_MarksLoopTarget_OnBackEdge()
    {
        var function = new FunctionExport
        {
            ScriptBytecode =
            [
                new EX_Jump { CodeOffset = 0 }
            ]
        };

        var cfg = BytecodeCommand.BuildCFG(new UAsset(), function);

        Assert.AreEqual(1, cfg.Blocks.Count);
        Assert.IsTrue(cfg.Blocks[0].IsLoopTarget);
        CollectionAssert.AreEqual(new[] { 0 }, cfg.Blocks[0].Successors);
    }
}

# Feature: Blueprint Graph Extraction (Pin Connections & Logic Flow)

## Problem

The asset parser currently extracts Blueprint **structure** (class hierarchy, components, function signatures, call lists) from compiled Kismet bytecode, but is blind to the **logic flow** — pin connections, data flow between nodes, branch conditions, and variable assignments. This means we can see *what* a function calls but not *how* the pieces connect.

This is the last major gap preventing full Blueprint comprehension (and eventual C++ porting).

## Current State

`AnalyzeExpression()` in `Program.cs` walks `FunctionExport.ScriptBytecode` and flattens everything into three `HashSet<string>`: `calls`, `variables`, `casts`. The tree structure of the bytecode is discarded.

K2Node exports (the visual Blueprint graph nodes with pin/connection data) are explicitly filtered out everywhere in the parser.

## Two Data Sources Available

### Source A: Compiled Kismet Bytecode (`FunctionExport.ScriptBytecode`)

- **Already fully deserialized by UAssetAPI** into typed `KismetExpression` trees
- `ScriptBytecode[]` is a flat array of top-level instructions, ordered as on disk
- Sub-expressions (function params, conditions) are nested as children on parent expressions
- Jump targets (`EX_Jump.CodeOffset`, `EX_JumpIfNot.CodeOffset`) are raw byte offsets
- UAssetAPI provides `Visit(asset, ref offset, visitor)` to walk expressions with byte offset tracking
- `KismetExpression.Tag` is a free annotation slot for attaching CFG node pointers
- **No CFG builder exists in UAssetAPI** — that's ours to build
- No per-expression result type — must be inferred from opcode or property lookup

Key control-flow instructions:
| Instruction | Fields | CFG Edge |
|---|---|---|
| `EX_Jump` | `CodeOffset` | Unconditional goto |
| `EX_JumpIfNot` | `CodeOffset`, `BooleanExpression` | Conditional branch (fall-through + jump) |
| `EX_SwitchValue` | `EndGotoOffset`, `Cases[].NextOffset`, `IndexTerm`, `DefaultTerm` | Multi-way branch |
| `EX_PushExecutionFlow` | `PushingAddress` | Push resume address onto flow stack |
| `EX_PopExecutionFlow` | (none) | Pop and resume from flow stack |
| `EX_Return` / `EX_EndOfScript` | | Exit node |
| `EX_ComputedJump` | `CodeOffsetExpression` | Dynamic target (cannot statically resolve) |

### Source B: K2Node Visual Graph (Editor Metadata)

- K2Nodes are `NormalExport` objects with generic `PropertyData` bags (no typed API)
- Each node has a `Pins` array of `StructPropertyData` (`StructType="EdGraphPin"`)
- Each pin contains: `PinName`, `PinType`, `Direction`, `LinkedTo[]`, `DefaultValue`
- `LinkedTo` is an `ArrayPropertyData` of `ObjectPropertyData` (FPackageIndex references to other pin exports)
- Node identity: export class type (e.g., `K2Node_CallFunction`, `K2Node_Branch`, `K2Node_VariableGet`)
- Layout data: `NodePosX`, `NodePosY` (not needed for logic extraction)
- `EEdGraphPinDirection` enum exists in UAssetAPI (`EGPD_Input=0`, `EGPD_Output=1`)

## Implementation Plan

### Phase 1: K2Node Pin Extraction (quick win)

Add a new parser command that reads the visual Blueprint graph and outputs node-to-node connections.

**Scope:**
- New command: `graph <uasset_path>` (or extend existing `blueprint` command)
- Walk `NormalExport` entries whose class type starts with `K2Node_`
- Extract `Pins` array from each node's `Data` property list
- Resolve `LinkedTo` references to build adjacency list
- Filter out layout-only data (NodePosX/Y, tooltips, hidden pins)
- Output as structured XML/JSON: nodes with their pins and connections

**Output format sketch:**
```xml
<graph>
  <function name="Lose Check">
    <node id="0" type="K2Node_CallFunction" target="Map_Find">
      <pin name="self" direction="in" type="Object" linked="node3:ReturnValue"/>
      <pin name="Key" direction="in" type="Enum" default="Food"/>
      <pin name="ReturnValue" direction="out" type="Int" linked="node1:A"/>
    </node>
    <node id="1" type="K2Node_CallFunction" target="LessEqual_IntInt">
      <pin name="A" direction="in" type="Int" linked="node0:ReturnValue"/>
      <pin name="B" direction="in" type="Int" default="0"/>
      <pin name="ReturnValue" direction="out" type="Bool" linked="node2:Condition"/>
    </node>
    <node id="2" type="K2Node_IfThenElse">
      <pin name="Condition" direction="in" type="Bool" linked="node1:ReturnValue"/>
      <pin name="Then" direction="out" type="Exec" linked="node4:Execute"/>
    </node>
  </function>
</graph>
```

### Phase 2: Bytecode Control Flow Graph

Build a proper CFG from the already-deserialized `ScriptBytecode[]`.

**Steps:**
1. Walk `ScriptBytecode[]`, call `expr.GetSize(asset)` to accumulate byte offsets → build `offset → index` map
2. Identify basic block boundaries at every jump target and jump source
3. Resolve `CodeOffset` fields through the offset map
4. Handle `EX_PushExecutionFlow` / `EX_PopExecutionFlow` (latent action flow stack)
5. Reconstruct expression trees per basic block (sub-expressions are already trees in UAssetAPI)
6. Emit structured pseudocode per function

**Output format sketch:**
```
block_0:
  FoodCount = Map_Find(ResourceMap, E_ResourceType::Food)
  branch (FoodCount <= 0) → block_1, fallthrough → block_2

block_1:
  EndGame(bWin=false)
  return

block_2:
  return
```

### Phase 3: Hybrid Cross-Reference

Correlate K2Node graph with bytecode for maximum fidelity.

- K2Nodes and bytecode reference the same functions/variables — cross-reference by name
- Use K2Node graph for: high-level flow, default pin values, designer intent
- Use bytecode for: precise execution semantics, compiler-resolved types, actual evaluation order
- Produce combined output: pseudocode with full data flow and constant values

## Comparison

| Aspect | K2Node Graph (Phase 1) | Bytecode CFG (Phase 2) | Hybrid (Phase 3) |
|---|---|---|---|
| Effort | Low (~200-400 lines C#) | Medium (~800-1200 lines C#) | Low (glue layer) |
| Shows data flow | Yes (pin connections) | Partial (variable assignments) | Full |
| Shows control flow | Partial (exec pin chains) | Full (jumps, loops, switches) | Full |
| Shows constants/defaults | Yes (DefaultValue on pins) | Yes (EX_*Const expressions) | Full |
| Handles macros | Pre-expansion (raw nodes) | Post-expansion (compiled) | Both views |
| Loop detection | No | Yes (back-edge analysis) | Yes |
| Accuracy | Editor fidelity | VM fidelity | Best of both |

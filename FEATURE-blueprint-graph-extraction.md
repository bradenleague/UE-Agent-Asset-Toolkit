# Feature: Blueprint Graph Extraction (Pin Connections & Logic Flow)

## Problem

The asset parser currently extracts Blueprint **structure** (class hierarchy, components, function signatures, call lists) from compiled Kismet bytecode, but is blind to the **logic flow** — pin connections, data flow between nodes, branch conditions, and variable assignments. This means we can see *what* a function calls but not *how* the pieces connect.

This is the last major gap preventing full Blueprint comprehension (and eventual C++ porting).

## Current State

**Phase 1 is complete.** The `graph` command (`ExtractGraph()` in Program.cs) parses K2Node
pin data from binary `Extras` blobs and outputs full node-to-node connection graphs. Exposed
via MCP as `inspect_blueprint_graph`. Output is JSON (should be converted to XML for
consistency with other commands).

**Phase 2 is complete.** The `bytecode` command (`ExtractBytecode()` + `BuildCFG()` +
`ExprToString()` in Program.cs) builds proper control flow graphs from compiled Kismet
bytecode and emits per-function pseudocode. Exposed via MCP as `inspect_blueprint_bytecode`.
Output is XML.

Validated on BP_GM (Cropout): 20 functions, 129 basic blocks, 469 pseudocode statements,
18 loop targets detected. Also validated on BP_Interactable and BP_Player.

The old flat `AnalyzeExpression()` still exists and is used by the `blueprint` command for
call/variable lists. The new `bytecode` command supersedes it for detailed analysis.

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
- Pin data is **not** in `PropertyData` — it's in the binary `Extras` blob, serialized by
  `UEdGraphNode::SerializeAsOwningNode` (see `ReadOnePin()` in Program.cs for the full format)
- Each pin contains: `PinName`, `PinType`, `Direction`, `LinkedTo[]`, `DefaultValue`
- `LinkedTo` references use GUID-based pin IDs resolved through a global pin GUID map
- Node identity: export class type (e.g., `K2Node_CallFunction`, `K2Node_Branch`, `K2Node_VariableGet`)
- Node target (function/variable name) resolved from `PropertyData` (e.g., `FunctionReference.MemberName`)
- Layout data: `NodePosX`, `NodePosY` (not needed for logic extraction)
- `EEdGraphPinDirection` enum: `EGPD_Input=0`, `EGPD_Output=1`

## Implementation Plan

### Phase 1: K2Node Pin Extraction — DONE

Implemented as `graph` command in Program.cs (`ExtractGraph()`, ~250 lines) + `ReadOnePin()` binary
pin parser (~160 lines). Exposed via MCP as `inspect_blueprint_graph` in tools.py.

**What it does:**
- Walks `NormalExport` entries whose class type starts with `K2Node_`
- Parses pin data from binary `Extras` blobs (not PropertyData — key discovery)
- Resolves `LinkedTo` GUID references through a global pin map to build adjacency lists
- Groups nodes by parent EdGraph (function name)
- Filters hidden/orphaned pins and unconnected nodes
- Resolves node targets (function names, variable names) from PropertyData structs

**Current output:** JSON. Should be converted to XML for consistency with `blueprint` command.

**TODO:** Convert output format from JSON to XML.

### Phase 2: Bytecode Control Flow Graph — DONE

Implemented as `bytecode` command in Program.cs (`ExtractBytecode()` + `BuildCFG()` + `ExprToString()`,
~450 lines C#). Exposed via MCP as `inspect_blueprint_bytecode` in tools.py.

**What it does:**
- Walks `ScriptBytecode[]`, accumulates byte offsets via `GetSize()` → builds offset→index map
- Identifies basic block boundaries at every jump target and jump source
- Resolves `CodeOffset` fields through offset map to block IDs
- Handles `EX_PushExecutionFlow` / `EX_PopExecutionFlow` / `EX_PopExecutionFlowIfNot`
- Detects loop targets via back-edge analysis (block targeting a predecessor)
- Converts 40+ expression types to readable pseudocode (function calls, variable access,
  assignments, casts, constants, delegates, collection ops, struct construction)
- Filters editor noise (tracepoints, instrumentation events)
- Outputs XML with `<block>` elements containing `<stmt>` pseudocode

**Validated on Cropout:** BP_GM (20 functions, 129 blocks, 469 statements, 18 loop targets),
BP_Interactable, BP_Player (1206 elements). Zero unhandled expression types.

**Output format (actual from BP_GM Lose Check):**
```xml
<function name="Lose Check" flags="Callable,Event">
  <block id="0" offset="0" successors="2,1">
    <stmt>Temp_byte_Variable = 1</stmt>
    <stmt>CallFunc_Map_Find_ReturnValue = Default__BlueprintMapLibrary.Map_Find(Resources, Temp_byte_Variable, CallFunc_Map_Find_Value)</stmt>
    <stmt>CallFunc_LessEqual_IntInt_ReturnValue = LessEqual_IntInt(CallFunc_Map_Find_Value, 0)</stmt>
    <stmt>if not (CallFunc_LessEqual_IntInt_ReturnValue) goto block_2</stmt>
  </block>
  <block id="1" offset="141" successors="2">
    <stmt>End Game(false)</stmt>
  </block>
  <block id="2" offset="159">
    <stmt>return</stmt>
  </block>
</function>
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
| Effort | **Done** (~410 lines C#) | **Done** (~450 lines C#) | Low (glue layer) |
| Shows data flow | Yes (pin connections) | Partial (variable assignments) | Full |
| Shows control flow | Partial (exec pin chains) | Full (jumps, loops, switches) | Full |
| Shows constants/defaults | Yes (DefaultValue on pins) | Yes (EX_*Const expressions) | Full |
| Handles macros | Pre-expansion (raw nodes) | Post-expansion (compiled) | Both views |
| Loop detection | No | Yes (back-edge analysis) | Yes |
| Accuracy | Editor fidelity | VM fidelity | Best of both |

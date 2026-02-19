# Feature: Blueprint Graph Extraction (Pin Connections & Logic Flow)

## Problem

The asset parser currently extracts Blueprint **structure** (class hierarchy, components, function signatures, call lists) from compiled Kismet bytecode, but is blind to the **logic flow** — pin connections, data flow between nodes, branch conditions, and variable assignments. This means we can see *what* a function calls but not *how* the pieces connect.

This is the last major gap preventing full Blueprint comprehension (and eventual C++ porting).

## Current State

**Phase 1 is complete.** The `graph` command (`ExtractGraph()` in Program.cs) parses K2Node
pin data from binary `Extras` blobs and outputs full node-to-node connection graphs. Exposed
via MCP through `inspect_asset(detail='graph')`. Output is JSON.

**Phase 2 is complete.** The `bytecode` command (`ExtractBytecode()` + `BuildCFG()` +
`ExprToString()` in Program.cs) builds proper control flow graphs from compiled Kismet
bytecode and emits per-function pseudocode. Available as a CLI command; **deprecated from
MCP** — the graph output (Phase 1) provides more actionable data at lower cost.

The `graph` command is routed through the `inspect_asset` MCP tool's `detail` parameter
(wired through `mcp_server.py` → `tools.py` → AssetParser CLI). A standalone tool
`inspect_blueprint_graph` exists as an internal helper.

Validated on BP_GM (Cropout): 20 functions, 129 basic blocks, 469 pseudocode statements,
18 loop targets detected. Also validated on BP_Interactable and BP_Player.

The old flat `AnalyzeExpression()` still exists and is used by the `blueprint` command for
call/variable lists.

### UE 5.7 Compatibility Fix

The Phase 1 pin parser (`ReadOnePin()`) required a fix for UE 5.7: the
`bSerializeAsSinglePrecisionFloat` field is gated by a custom version
(`FUE5ReleaseStreamObjectVersion.SerializeFloatPinDefaultValuesAsSinglePrecision`),
not by a coarse engine version check. Without this, the pin binary reader would overshoot
the stream on 5.7 assets.

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
pin parser (~160 lines). Exposed via MCP as `inspect_asset(detail='graph')`.

**What it does:**
- Walks `NormalExport` entries whose class type starts with `K2Node_`
- Parses pin data from binary `Extras` blobs (not PropertyData — key discovery)
- Resolves `LinkedTo` GUID references through a global pin map to build adjacency lists
- Groups nodes by parent EdGraph (function name)
- Filters hidden/orphaned pins and unconnected nodes
- Resolves node targets (function names, variable names) from PropertyData structs

**Output:** JSON with nodes, pins, and connections grouped by function.

### Phase 2: Bytecode Control Flow Graph — DONE

Implemented as `bytecode` CLI command in Program.cs (`ExtractBytecode()` + `BuildCFG()` + `ExprToString()`,
~450 lines C#). Deprecated from MCP — graph output (Phase 1) is more actionable.

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

### Phase 3: Toward C++ Porting — NOT STARTED

With Phases 1 and 2 complete, we can fully understand what a Blueprint *does*. What's
still missing is metadata needed for a faithful C++ port. This breaks into two sub-problems:

#### 3a: Member Variable Declarations

The bytecode shows variables being *used* (`Resources`, `Town Hall`, `SpawnRef`, `UI_HUD`,
`Villager Count`) but doesn't declare them. For C++ we need:

- Exact UE types (`TMap<EResourceType, int32>`, `TSoftClassPtr<AActor>`, etc.)
- UPROPERTY specifiers (EditAnywhere, BlueprintReadWrite, Replicated, Category, etc.)
- Default values

**Approach:** Add a `properties` command that dumps `FPropertyExport` / `PropertyData` from
the class default object. UAssetAPI already deserializes these — we just need to format them.

#### 3b: Ubergraph Decomposition

Blueprint compiles all event handlers into a single `ExecuteUbergraph_*` function with
`EX_ComputedJump` dispatch based on an `EntryPoint` offset. In C++, each event is a
separate function. We need to:

- Map each stub function's entry point offset (e.g., `ExecuteUbergraph_BP_GM(3006)`) to
  the corresponding block range in the ubergraph
- Slice the ubergraph into independent event handler regions
- Re-emit each region as a standalone function

**Approach:** Walk stub functions, collect their `EntryPoint` constants, trace reachable
blocks from each entry point in the CFG, and partition the ubergraph.

#### 3c: Additional Metadata for Faithful Porting

- **Delegate type signatures** — `DECLARE_DYNAMIC_MULTICAST_DELEGATE` macro parameters.
  Visible from delegate call sites in bytecode (parameter names/types) but not formally declared.
- **UFUNCTION metadata** — BlueprintCallable, BlueprintPure, Category strings. Stored in
  `FunctionExport.FunctionFlags` (partially extracted) but Category/DisplayName are in
  field-level metadata we don't currently read.
- **Soft/asset references** — `TownHall_Ref`, `Villager_Ref` are soft class refs; need to
  resolve the exact `TSoftClassPtr<T>` template type from the property declaration.
- **Latent action translation** — `Delay()`, `LoadAssetClass()`, `DelayUntilNextTick()` use
  Blueprint's latent system with `LatentActionInfo`. These map to `FTimerHandle`,
  `FStreamableManager`, or custom latent actions in C++.
- **Interface dependencies** — need to inspect referenced Blueprints (BPI_GI, BPI_Villager,
  etc.) to know their full interface signatures.

#### 3d: Hybrid Cross-Reference (Original Phase 3 Idea)

Correlate K2Node graph with bytecode for maximum fidelity.

- K2Nodes and bytecode reference the same functions/variables — cross-reference by name
- Use K2Node graph for: high-level flow, default pin values, designer intent
- Use bytecode for: precise execution semantics, compiler-resolved types, actual evaluation order
- Produce combined output: pseudocode with full data flow and constant values

## Comparison

| Aspect | K2Node Graph (Phase 1) | Bytecode CFG (Phase 2) | With Phase 3 |
|---|---|---|---|
| Effort | **Done** (~410 lines C#) | **Done** (~450 lines C#) | Not started |
| Shows data flow | Yes (pin connections) | Partial (variable assignments) | Full |
| Shows control flow | Partial (exec pin chains) | Full (jumps, loops, switches) | Full |
| Shows constants/defaults | Yes (DefaultValue on pins) | Yes (EX_*Const expressions) | Full |
| Handles macros | Pre-expansion (raw nodes) | Post-expansion (compiled) | Both views |
| Loop detection | No | Yes (back-edge analysis) | Yes |
| Accuracy | Editor fidelity | VM fidelity | Best of both |
| Member variable types | Pin types (partial) | Inferred from usage | Explicit declarations |
| Ubergraph decomposition | N/A (pre-compilation) | Flat mega-function | Per-event functions |
| C++ portability | ~60% | ~75% | ~95% |

## What Can We Do Today

With Phases 1+2, an LLM or developer can:
- **Fully understand** what any Blueprint function does (logic, branches, loops, calls)
- **Explain** a Blueprint's behavior in plain English (demonstrated on BP_GM)
- **Trace** data flow through pin connections and variable assignments
- **Identify** dependencies, interfaces, and external calls
- **Draft** a C++ port that captures the logic correctly, with manual type annotation

What requires Phase 3:
- **Automated** C++ code generation with correct UPROPERTY/UFUNCTION declarations
- **Decomposed** event handlers (instead of monolithic ubergraph)
- **Faithful** delegate and latent action translation

# Graph Compact XML Benchmark

`graph-plus-json` command now outputs compact XML instead of JSON.

## Size Comparison

| Asset | JSON (bytes) | XML (bytes) | Reduction |
|-------|-------------|-------------|-----------|
| GA_Hero_Death (small ability) | 10,376 | 3,357 | **67.6%** |
| W_WeaponReticleHost (medium widget) | 38,440 | 12,906 | **66.4%** |
| B_Hero_ShooterMannequin (large BP) | 117,287 | 39,979 | **65.9%** |

## Semantic Preservation (GA_Hero_Death)

| Data | JSON | Compact XML |
|------|------|-------------|
| Graph nodes | 6 | 6 |
| Graph functions | 1 (EventGraph) | 1 (EventGraph) |
| Member variables | 0 | 0 |
| Metadata functions | 3 (incl. ExecuteUbergraph) | 2 (ExecuteUbergraph filtered) |
| Delegate declarations | 0 | 0 |
| Delegate bindings | 2 | 2 |

All node IDs, connections, targets, pin types, and defaults preserved.

## What Changed

- **Short node IDs**: `N1`, `N2` instead of raw export indices (e.g., `47`, `52`)
- **Directional pin groups**: `<in>`/`<out>` eliminate per-pin `dir` attribute
- **Implicit exec**: `cat` attribute omitted when pin category is `exec`
- **Output-only connections**: `to` on output pins only; input pins only show inline refs (`var:X`, `self`)
- **Debug fields dropped**: `raw_property_flags`, `raw_type_tokens`, `raw_flags`, `specifiers` on params, empty `metadata`/`metadata_source`
- **ExecuteUbergraph filtered** from both functions list and graph
- **Compact tags**: `<m>`, `<fn>`, `<p>`, `<N>`

## Contract

Root element: `<graph-plus contract="graph_compact_v1">`

Previous contract `graph_plus_v1` (JSON) is replaced. `graph-summary-json` remains unchanged as JSON (`graph_summary_v1`).

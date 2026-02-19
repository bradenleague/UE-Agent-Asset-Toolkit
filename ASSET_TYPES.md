# Asset Type Handling Reference

This document tracks how different Unreal Engine asset types are handled by the AssetParser and UnrealAgent tools.

## Overview

The goal is to provide **focused, contextually relevant data** for each asset type instead of dumping raw properties. This reduces token usage and improves agent efficiency.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Agent calls inspect_asset(path, summarize=True)                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  tools.py: Runs 'summary' command to detect asset type          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Routes to specialized AssetParser command based on type        │
│                                                                 │
│  Material/MaterialInstance  →  material                         │
│  WidgetBlueprint            →  widgets                          │
│  DataTable                  →  datatable                        │
│  Blueprint                  →  blueprint                        │
│  Other                      →  inspect (full dump)              │
└─────────────────────────────────────────────────────────────────┘
```

## Asset Type Status

| Asset Type | Command | Status | Extracts | Notes |
|------------|---------|--------|----------|-------|
| **Material** | `material` | ✅ Done | Domain, blend mode, shading model | Base materials have params in expressions |
| **MaterialInstance** | `material` | ✅ Done | Parent, scalar/vector/texture params, static switches | Full parameter extraction |
| **WidgetBlueprint** | `widgets` | ✅ Done | Widget tree, properties, parent-child hierarchy | MVVM bindings via subagent |
| **DataTable** | `datatable` | ✅ Done | Row struct type, all rows with values | Works with any row struct |
| **Blueprint** | `blueprint` | ✅ Done | Functions (calls, variables, casts), class hierarchy, events | `detail='graph'` for node wiring |
| DataAsset | `inspect` | ⚠️ Generic | All properties | Could benefit from type-specific extraction |
| Texture2D | `inspect` | ⚠️ Generic | All properties | Could extract: dimensions, format, compression |
| StaticMesh | `inspect` | ⚠️ Generic | All properties | Could extract: LODs, materials, collision |
| SkeletalMesh | `inspect` | ⚠️ Generic | All properties | Could extract: bones, sockets, materials |
| AnimSequence | `inspect` | ⚠️ Generic | All properties | Could extract: length, curves, notifies |
| AnimMontage | `inspect` | ⚠️ Generic | All properties | Could extract: sections, notifies, slots |
| AnimBlueprint | `blueprint` | ⚠️ Partial | Functions only | Could extract: state machines, blend spaces |
| SoundWave | `inspect` | ⚠️ Generic | All properties | Could extract: duration, channels, sample rate |
| SoundCue | `inspect` | ⚠️ Generic | All properties | Could extract: node graph summary |
| NiagaraSystem | `inspect` | ⚠️ Generic | All properties | Complex - may need dedicated handler |
| Level/World | `inspect` | ⚠️ Generic | All properties | Could extract: actor list, streaming levels |
| GameplayAbility | `inspect` | ⚠️ Generic | All properties | Could extract: tags, costs, cooldowns |
| GameplayEffect | `inspect` | ⚠️ Generic | All properties | Could extract: modifiers, duration, tags |

### Legend
- ✅ **Done**: Full specialized extraction implemented
- ⚠️ **Generic**: Falls back to full property dump
- ⚠️ **Partial**: Some extraction but could be improved
- ❌ **Not Supported**: Known issues or not implemented

## Implementation Details

All specialized commands output **XML** for efficient LLM parsing.

### Material (`material` command)

**File**: `AssetParser/Program.cs` - `ExtractMaterial()`

**Example Output**:
```xml
<material-instance>
  <name>MI_UI_Button_Base</name>
  <parent>M_UI_AngledBox_Base</parent>
  <domain>Surface</domain>
  <blend-mode>Opaque</blend-mode>
  <shading-model>DefaultLit</shading-model>
  <parameters>
    <scalar name="Rotation_U" value="0" />
    <vector name="RBGA1" rgba="0,0,0,1" />
    <texture name="BaseTexture" ref="T_UI_Base" group="Textures" />
  </parameters>
  <static-switches>
    <switch name="PixelBorder" value="false" />
  </static-switches>
</material-instance>
```

### WidgetBlueprint (`widgets` command)

**File**: `AssetParser/Program.cs` - `ExtractWidgets()`

**Example Output**:
```xml
<widget-blueprint>
  <summary widget-count="12" />
  <hierarchy>
    <widget name="Overlay_0" type="Overlay">
      <widget name="Border_1" type="Border">
        <widget name="Button_Start" type="Button" />
        <widget name="Text_Title" type="TextBlock" text="Main Menu" />
      </widget>
    </widget>
  </hierarchy>
</widget-blueprint>
```

Hierarchy is built by tracing the slot system to find actual parent-child relationships.

### DataTable (`datatable` command)

**File**: `AssetParser/Program.cs` - `ExtractDataTable()`

**Example Output**:
```xml
<datatable>
  <row-struct>FWeaponData</row-struct>
  <row-count>15</row-count>
  <columns>
    <column name="Damage" type="Float" />
    <column name="FireRate" type="Float" />
  </columns>
  <rows>
    <row key="Pistol" Damage="25" FireRate="0.3" />
    <row key="Rifle" Damage="35" FireRate="0.1" />
    <!-- and 13 more rows -->
  </rows>
</datatable>
```

Rows are truncated at 25 to prevent huge outputs.

### Blueprint (`blueprint` command)

**File**: `AssetParser/Program.cs` - `ExtractBlueprint()`

**Example Output**:
```xml
<blueprint>
  <name>BP_Player</name>
  <parent>Character</parent>
  <interfaces>
    <interface>BPI_Damageable</interface>
  </interfaces>
  <components>
    <component type="CameraComponent">PlayerCamera</component>
    <component type="SpringArmComponent">CameraBoom</component>
  </components>
  <events>
    <event>ReceiveBeginPlay</event>
    <event>ReceiveTick</event>
  </events>
  <functions>
    <function name="TakeDamage" flags="Callable,Event">
      <calls>ApplyDamage, UpdateHealthUI, CheckDeath</calls>
    </function>
    <function name="Heal" flags="Callable">
      <calls>Clamp, SetHealth</calls>
    </function>
  </functions>
  <variables>
    <variable type="Float">Health</variable>
    <variable type="Float">MaxHealth</variable>
  </variables>
</blueprint>
```

**Key feature**: Functions include `<calls>` showing what they actually do (call targets, variable access, casts).

## Adding New Asset Type Handlers

### 1. Add AssetParser Command (C#)

In `AssetParser/Program.cs`:

```csharp
// 1. Add to switch statement in Main()
case "mytype":
    ExtractMyType(asset);
    break;

// 2. Implement extraction function (output XML)
static void ExtractMyType(UAsset asset)
{
    var xml = new System.Text.StringBuilder();
    xml.AppendLine("<mytype>");
    xml.AppendLine($"  <name>{EscapeXml(asset.Exports[0].ObjectName.ToString())}</name>");
    // ... extract relevant properties as XML
    xml.AppendLine("</mytype>");
    Console.WriteLine(xml.ToString());
}
```

### 2. Add Type Detection

In `AssetParser/Program.cs` `SummarizeAsset()`:

```csharp
else if (exportClasses.Any(c => c.Contains("MyType")))
    assetType = "MyType";
```

### 3. Wire Up Smart Routing

In `UnrealAgent/tools.py` `inspect_asset()`:

```python
if asset_type == "MyType":
    return _run_asset_parser("mytype", file_path)
```

### 4. Update Documentation

- `AssetParser/README.md`: Add command to table
- `ASSET_TYPES.md`: Update status table

## Priority Queue

Assets that would benefit most from specialized extraction:

1. **AnimBlueprint** - State machines are complex, raw dump is unhelpful
2. **Texture2D** - Quick metadata (dimensions, format) would be useful
3. **StaticMesh/SkeletalMesh** - LOD counts, material slots, bounds
4. **GameplayAbility/GameplayEffect** - GAS-specific properties
5. **NiagaraSystem** - Very complex, needs careful handling

## Testing

Test new handlers with:

```bash
# Direct AssetParser test
cd AssetParser && dotnet build -c Release
./bin/Release/net8.0/AssetParser.exe mytype "Content/Path/To/Asset.uasset"

# Python integration test (from UnrealAgent directory)
cd UnrealAgent
python -c "
from tools import inspect_asset
print(inspect_asset('/Game/Path/To/Asset', summarize=True))
"

# MCP server test
python -c "
from mcp_server import inspect_asset
result = inspect_asset('/Game/Path/To/Asset')
print(result)
"
```

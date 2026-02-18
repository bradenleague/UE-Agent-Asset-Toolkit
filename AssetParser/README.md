# AssetParser

CLI tool for extracting data from Unreal Engine `.uasset` files without requiring the editor.

Built on [UAssetAPI](https://github.com/atenfyr/UAssetAPI).

## Build

```bash
cd Tools/AssetParser
dotnet build -c Release
```

Output: `bin/Release/net8.0/AssetParser.exe`

## Commands

```bash
AssetParser.exe <command> <asset_path> [--version UE5_7]
```

| Command | Description |
|---------|-------------|
| `summary` | Quick asset type detection and overview |
| `inspect` | Dump all exports and properties |
| `widgets` | Extract widget tree from Widget Blueprints |
| `datatable` | Extract rows from DataTable assets |
| `blueprint` | Extract functions with bytecode analysis |
| `material` | Extract Material/MaterialInstance parameters |

## Examples

```bash
# Quick summary
AssetParser.exe summary Content/Blueprints/MyWidget.uasset

# Full property dump
AssetParser.exe inspect Content/Data/EnemyConfig.uasset

# Widget hierarchy
AssetParser.exe widgets Content/Blueprints/MainMenu.uasset

# DataTable rows
AssetParser.exe datatable Content/Data/ItemDatabase.uasset

# Blueprint analysis (shows function calls, variables, casts)
AssetParser.exe blueprint Content/Blueprints/PlayerController.uasset

# Material parameters (scalar, vector, texture)
AssetParser.exe material Content/Materials/MI_Button.uasset
```

## Output Format

All specialized commands output **XML** for efficient LLM parsing (no multi-page scrolling through JSON dumps).

### Blueprint

```xml
<blueprint>
  <name>BP_Player</name>
  <parent>Character</parent>
  <components>
    <component type="CameraComponent">PlayerCamera</component>
  </components>
  <events>
    <event>ReceiveBeginPlay</event>
  </events>
  <functions>
    <function name="TakeDamage" flags="Callable,Event">
      <calls>ApplyDamage, UpdateHealthUI, CheckDeath</calls>
    </function>
  </functions>
  <variables>
    <variable type="Float">Health</variable>
  </variables>
</blueprint>
```

### Widget Blueprint

```xml
<widget-blueprint>
  <summary widget-count="8" />
  <hierarchy>
    <widget name="Overlay_0" type="Overlay">
      <widget name="Button_Start" type="Button" />
      <widget name="Text_Title" type="TextBlock" text="Main Menu" />
    </widget>
  </hierarchy>
</widget-blueprint>
```

### Material

```xml
<material-instance>
  <name>MI_Character</name>
  <parent>M_Character_Base</parent>
  <domain>Surface</domain>
  <blend-mode>Opaque</blend-mode>
  <parameters>
    <scalar name="Roughness" value="0.5" />
    <vector name="BaseColor" rgba="1,0.8,0.6,1" />
    <texture name="DiffuseMap" ref="T_Character_D" />
  </parameters>
</material-instance>
```

### DataTable

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
  </rows>
</datatable>
```

## File Locking

Unreal Editor locks `.uasset` files while open. If you get "file is being used by another process":

1. Close Unreal Editor, or
2. Unload the specific asset, or
3. Copy the file and inspect the copy

## Engine Version

Default is UE5_7. Override with `--version`:

```bash
AssetParser.exe summary MyAsset.uasset --version UE5_3
```

Supported: `UE4_0` through `UE5_7`

## See Also

- [Asset Type Handling Reference](../ASSET_TYPES.md) - Status of specialized extraction per asset type
- [UAssetAPI](https://github.com/atenfyr/UAssetAPI) - Underlying parsing library

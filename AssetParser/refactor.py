import os
import re

with open("Program.cs.bak", "r") as f:
    code = f.read()

# Split the code using the section banners
sections_raw = re.split(r'// ============================================================================\n(.*?)\n// ============================================================================\n', code, flags=re.DOTALL)

usings = sections_raw[0]

sections = {}
for i in range(1, len(sections_raw), 2):
    title_block = sections_raw[i].strip()
    first_line = title_block.split('\n')[0].replace('// ', '').strip()
    content = sections_raw[i+1]
    sections[first_line] = content

file_map = {
    "ASSET TYPE DETECTION TABLES": ("Core/AssetTypeDetector.cs", "AssetParser.Core", "AssetTypeDetector"),
    "MAIN ENTRY POINT": None,
    "SUMMARY - Quick asset detection": ("Commands/SummaryCommand.cs", "AssetParser.Commands", "SummaryCommand"),
    "INSPECT - Generic full dump": ("Commands/InspectCommand.cs", "AssetParser.Commands", "InspectCommand"),
    "WIDGETS - Widget Blueprint extraction as XML": ("Commands/WidgetCommand.cs", "AssetParser.Commands", "WidgetCommand"),
    "DATATABLE - Extract DataTable rows as XML": ("Commands/DataTableCommand.cs", "AssetParser.Commands", "DataTableCommand"),
    "BLUEPRINT - Extract Blueprint as focused XML": ("Commands/BlueprintCommand.cs", "AssetParser.Commands", "BlueprintCommand"),
    "GRAPH - Extract Blueprint node graph with pin connections (XML default, JSON via graph-json)": ("Commands/GraphCommand.cs", "AssetParser.Commands", "GraphCommand"),
    "BYTECODE - Extract control flow graph and pseudocode from Kismet bytecode": ("Commands/BytecodeCommand.cs", "AssetParser.Commands", "BytecodeCommand"),
    "MATERIAL - Extract Material/MaterialInstance parameters as XML": ("Commands/MaterialCommand.cs", "AssetParser.Commands", "MaterialCommand"),
    "MATERIAL FUNCTION - Extract MaterialFunction inputs, outputs, and parameters": ("Commands/MaterialFunctionCommand.cs", "AssetParser.Commands", "MaterialFunctionCommand"),
    "REFERENCES - Extract all asset references": ("Commands/ReferencesCommand.cs", "AssetParser.Commands", "ReferencesCommand"),
    "BYTECODE ANALYSIS": ("Parsers/BytecodeAnalyzer.cs", "AssetParser.Parsers", "BytecodeAnalyzer"),
    "CONTROL FLOW ANALYSIS - Extract control flow summary from bytecode": ("Parsers/ControlFlowAnalyzer.cs", "AssetParser.Parsers", "ControlFlowAnalyzer"),
    "HELPERS": ("Core/Helpers.cs", "AssetParser.Core", "Helpers"),
    "BATCH OPERATIONS - For high-performance indexing (430x speedup)": ("Commands/BatchCommands.cs", "AssetParser.Commands", "BatchCommands"),
    "BATCH BLUEPRINT - Extract blueprint data for multiple assets as JSONL": ("Commands/BatchBlueprintCommand.cs", "AssetParser.Commands", "BatchBlueprintCommand"),
    "BATCH WIDGET - Extract widget data for multiple assets as JSONL": ("Commands/BatchWidgetCommand.cs", "AssetParser.Commands", "BatchWidgetCommand"),
    "BATCH MATERIAL - Extract material data for multiple assets as JSONL": ("Commands/BatchMaterialCommand.cs", "AssetParser.Commands", "BatchMaterialCommand"),
    "BATCH DATATABLE - Extract datatable data for multiple assets as JSONL": ("Commands/BatchDataTableCommand.cs", "AssetParser.Commands", "BatchDataTableCommand"),
    "HELPER: Collect asset references (shared by batch commands)": ("Core/AssetRefHelper.cs", "AssetParser.Core", "AssetRefHelper")
}

common_usings = """using System;
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
"""

program_context = """using System;
using UAssetAPI;
using UAssetAPI.UnrealTypes;

namespace AssetParser.Core
{
    public static class ProgramContext
    {
        public static string[] args;
        public static string assetPath;
        public static EngineVersion engineVersion;
        public static UAsset currentAsset;
        
        // Add stub to avoid compile errors if something sneaks through
        public static string EscapeXml(string text) => text;
    }
}
"""

for title, content in sections.items():
    if title not in file_map:
        continue
    
    mapping = file_map[title]
    if mapping is None:
        continue
        
    filepath, ns, classname = mapping

    content = re.sub(r'var\s+([A-Z][a-zA-Z0-9_]+)\s*=', r'public static var \1 =', content) 
    content = content.replace("public static var NamingPrefixes", "public static Dictionary<string, string> NamingPrefixes")
    content = content.replace("public static var ExactClassTypes", "public static Dictionary<string, string> ExactClassTypes")
    content = content.replace("public static var StructuralIndicators", "public static Dictionary<string, string> StructuralIndicators")
    
    # Extract structural classes first
    types_block = ""
    if "HELPER: Collect" in title:
        parts = content.split("// Type declarations must follow all top-level statements")
        content = parts[0]
        if len(parts) > 1:
            types_block = parts[1]
            types_block = re.sub(r'^struct', 'public struct', types_block, flags=re.MULTILINE)
            types_block = re.sub(r'^class', 'public class', types_block, flags=re.MULTILINE)
            
    # Convert top-level functions (0 spaces indented) to public static methods
    content = re.sub(r'^([a-zA-Z\(][A-Za-z0-9_<>\[\],\?\(\) ]+?)\s+([A-Z][A-Za-z0-9_]+)\s*\(', r'public static \1 \2(', content, flags=re.MULTILINE)
    
    # Clean up double public static if it accidentally matched
    content = content.replace("public static public static", "public static")

    # Fix closures
    content = re.sub(r'\bargs\b', 'ProgramContext.args', content)
    content = re.sub(r'\bassetPath\b', 'ProgramContext.assetPath', content)
    content = re.sub(r'\bengineVersion\b', 'ProgramContext.engineVersion', content)
    content = re.sub(r'\bcurrentAsset\b', 'ProgramContext.currentAsset', content)
    
    # Undo replacements inside method signatures
    content = content.replace("EngineVersion ProgramContext.engineVersion", "EngineVersion engineVersion")
    content = content.replace("UAsset ProgramContext.currentAsset", "UAsset currentAsset")
    content = content.replace("string ProgramContext.assetPath", "string assetPath")
    content = content.replace("string[] ProgramContext.args", "string[] args")
    content = content.replace("List<string> ProgramContext.args", "List<string> args")

    full_code = common_usings + f"\nnamespace {ns}\n{{\n" + f"    public static class {classname}\n    {{\n"
    
    for line in content.split("\n"):
        full_code += "        " + line + "\n"
        
    full_code += "    }\n"
    
    if types_block:
        full_code += types_block + "\n"
        
    full_code += "}\n"
    
    with open(filepath, "w") as out:
        out.write(full_code)

with open("Core/ProgramContext.cs", "w") as out:
    out.write(program_context)

main_code = sections["MAIN ENTRY POINT"]

# Route locals to ProgramContext in MAIN ENTRY POINT but KEEP the local variables so Program.cs compiles!
main_code = re.sub(r'\bargs\b', 'ProgramContext.args', main_code, count=1) # only assign once if we could, but args is an implicit parameter, so we just add an assignment at the top.
main_code = "        ProgramContext.args = args;\n" + main_code
main_code = re.sub(r'string assetPath =', 'string assetPath = ProgramContext.assetPath =', main_code)
main_code = re.sub(r'EngineVersion engineVersion =', 'EngineVersion engineVersion = ProgramContext.engineVersion =', main_code)
main_code = re.sub(r'UAsset\? currentAsset = null;', 'UAsset? currentAsset = null;\n        ProgramContext.currentAsset = null;', main_code)
main_code = re.sub(r'currentAsset = asset;', 'currentAsset = ProgramContext.currentAsset = asset;', main_code)

new_main = usings + "\n" + common_usings + "\n" + main_code

with open("Program.cs", "w") as out:
    out.write(new_main)

print("Extraction complete!")

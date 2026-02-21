import os
import re

with open("Program.cs", "r") as f:
    code = f.read()

# Split the code using the section banners
sections_raw = re.split(r'// ============================================================================\s*\n//(.*?)\n// ============================================================================\s*\n', code)

preamble = sections_raw[0]

sections = {}
for i in range(1, len(sections_raw), 2):
    title = sections_raw[i].strip()
    content = sections_raw[i+1]
    sections[title] = content

# Write out the preamble + Main to a new Program.cs
main_content = preamble + \
    "// ============================================================================\n" + \
    "// MAIN ENTRY POINT\n" + \
    "// ============================================================================\n" + \
    sections["MAIN ENTRY POINT"]

# We need to replace calls to these methods with calls to the new classes
# e.g. SummarizeAsset(asset) -> SummaryCommand.SummarizeAsset(asset, assetPath, engineVersion)

commands_map = {
    "SUMMARY - Quick asset detection": ("Commands/SummaryCommand.cs", "SummaryCommand"),
    "INSPECT - Generic full dump": ("Commands/InspectCommand.cs", "InspectCommand"),
    "WIDGETS - Widget Blueprint extraction as XML": ("Commands/WidgetCommand.cs", "WidgetCommand"),
    "DATATABLE - Extract DataTable rows as XML": ("Commands/DataTableCommand.cs", "DataTableCommand"),
    "BLUEPRINT - Extract Blueprint as focused XML": ("Commands/BlueprintCommand.cs", "BlueprintCommand"),
    "GRAPH - Extract Blueprint node graph with pin connections (XML default, JSON via graph-json)": ("Commands/GraphCommand.cs", "GraphCommand"),
    "BYTECODE - Extract control flow graph and pseudocode from Kismet bytecode": ("Commands/BytecodeCommand.cs", "BytecodeCommand"),
    "MATERIAL - Extract Material/MaterialInstance parameters as XML": ("Commands/MaterialCommand.cs", "MaterialCommand"),
    "MATERIAL FUNCTION - Extract MaterialFunction inputs, outputs, and parameters": ("Commands/MaterialFunctionCommand.cs", "MaterialFunctionCommand"),
    "REFERENCES - Extract all asset references": ("Commands/ReferencesCommand.cs", "ReferencesCommand")
}

batch_commands_map = {
    "BATCH OPERATIONS - For high-performance indexing (430x speedup)": "BatchOperations",
    "BATCH BLUEPRINT - Extract blueprint data for multiple assets as JSONL": "BatchBlueprintCommand",
    "BATCH WIDGET - Extract widget data for multiple assets as JSONL": "BatchWidgetCommand",
    "BATCH MATERIAL - Extract material data for multiple assets as JSONL": "BatchMaterialCommand",
    "BATCH DATATABLE - Extract datatable data for multiple assets as JSONL": "BatchDataTableCommand"
}

def wrap_class(ns, class_name, content, usings):
    # We add `public static` to functions to make them accessible
    content = re.sub(r'^(\s*)([a-zA-Z0-9_<>\[\]]+)\s+([A-Z][a-zA-Z0-9_]+)\(', r'\1public static \2 \3(', content, flags=re.MULTILINE)
    
    # Very naive injection of parameters - it's better to manually fix or just keep them as is and fix compile errors
    
    code = usings + "\n\nnamespace " + ns + "\n{\n    public static class " + class_name + "\n    {\n"
    for line in content.split('\n'):
        code += "        " + line + "\n"
    code += "    }\n}\n"
    return code

print("Sections found:", list(sections.keys()))

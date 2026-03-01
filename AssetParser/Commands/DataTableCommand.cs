using System;
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

namespace AssetParser.Commands
{
    public static class DataTableCommand
    {
        public static void ExtractDataTable(UAsset asset)
        {
            var xml = new System.Text.StringBuilder();
        
            var dtExport = asset.Exports.OfType<DataTableExport>().FirstOrDefault();
            if (dtExport?.Table?.Data == null)
            {
                xml.AppendLine("<datatable>");
                xml.AppendLine("  <error>No DataTable found in asset</error>");
                xml.AppendLine("</datatable>");
                Console.WriteLine(xml.ToString());
                return;
            }
        
            var rowStruct = dtExport.Table.Data.FirstOrDefault()?.StructType?.ToString() ?? "Unknown";
            var rowCount = dtExport.Table.Data.Count;
        
            xml.AppendLine("<datatable>");
            xml.AppendLine($"  <row-struct>{EscapeXml(rowStruct)}</row-struct>");
            xml.AppendLine($"  <row-count>{rowCount}</row-count>");
        
            // Extract column names from first row
            if (dtExport.Table.Data.Count > 0 && dtExport.Table.Data[0].Value != null)
            {
                xml.AppendLine("  <columns>");
                foreach (var prop in dtExport.Table.Data[0].Value)
                {
                    var colName = prop.Name.ToString();
                    var colType = prop.PropertyType?.ToString() ?? "Unknown";
                    xml.AppendLine($"    <column name=\"{EscapeXml(colName)}\" type=\"{EscapeXml(colType)}\" />");
                }
                xml.AppendLine("  </columns>");
            }
        
            xml.AppendLine("  <rows>");
        
            // Limit rows to avoid huge output
            const int maxRows = 25;
            var rowsToShow = dtExport.Table.Data.Take(maxRows);
        
            foreach (var row in rowsToShow)
            {
                var rowName = row.Name.ToString();
                xml.Append($"    <row key=\"{EscapeXml(rowName)}\"");
        
                if (row.Value != null)
                {
                    // For simple rows, inline as attributes
                    if (row.Value.Count <= 6 && row.Value.All(p => IsSimpleProperty(p)))
                    {
                        foreach (var prop in row.Value)
                        {
                            var propName = prop.Name.ToString();
                            var propVal = GetPropertyValue(prop, 0);
                            xml.Append($" {EscapeXml(propName)}=\"{EscapeXml(propVal?.ToString() ?? "")}\"");
                        }
                        xml.AppendLine(" />");
                    }
                    else
                    {
                        // Complex row - use nested elements
                        xml.AppendLine(">");
                        foreach (var prop in row.Value)
                        {
                            var propName = prop.Name.ToString();
                            var propVal = GetPropertyValue(prop, 1);
                            if (propVal is Dictionary<string, object> dict)
                            {
                                xml.AppendLine($"      <{EscapeXml(propName)}>{FormatDictAsAttributes(dict)}</{EscapeXml(propName)}>");
                            }
                            else
                            {
                                xml.AppendLine($"      <{EscapeXml(propName)}>{EscapeXml(propVal?.ToString() ?? "")}</{EscapeXml(propName)}>");
                            }
                        }
                        xml.AppendLine("    </row>");
                    }
                }
                else
                {
                    xml.AppendLine(" />");
                }
            }
        
            if (rowCount > maxRows)
                xml.AppendLine($"    <!-- and {rowCount - maxRows} more rows -->");
        
            xml.AppendLine("  </rows>");
            xml.AppendLine("</datatable>");
        
            Console.WriteLine(xml.ToString());
        }
        
        public static bool IsSimpleProperty(PropertyData prop)
        {
            return prop is IntPropertyData || prop is FloatPropertyData || prop is DoublePropertyData ||
                   prop is BoolPropertyData || prop is StrPropertyData || prop is NamePropertyData ||
                   prop is BytePropertyData || prop is EnumPropertyData;
        }
        
        public static string FormatDictAsAttributes(Dictionary<string, object> dict)
        {
            return string.Join(" ", dict.Select(kv => $"{kv.Key}=\"{EscapeXml(FormatValue(kv.Value))}\""));
        }
        
        public static string FormatValue(object value)
        {
            if (value == null) return "";
        
            if (value is Dictionary<string, object> nestedDict)
            {
                // Format nested struct as key=value pairs
                var parts = nestedDict.Select(kv => $"{kv.Key}={FormatValue(kv.Value)}");
                return "{" + string.Join(", ", parts) + "}";
            }
        
            if (value is List<object> list)
            {
                // Format list as comma-separated values
                var items = list.Select(item => FormatValue(item));
                return "[" + string.Join(", ", items) + "]";
            }
        
            return value.ToString() ?? "";
        }
        
        
    }
}

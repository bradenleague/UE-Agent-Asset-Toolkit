using System;
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

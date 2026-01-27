"""
C++ Parser - Regex-based extraction of UE macros and basic C++ structure.

Extracts:
- UCLASS declarations with specifiers and parent class
- UFUNCTION declarations with specifiers and signatures
- UPROPERTY declarations with specifiers and types
- Regular C++ classes and functions (fallback)
- #include statements
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class UPropertyInfo:
    """Information about a UPROPERTY declaration."""
    name: str
    type: str
    specifiers: list[str] = field(default_factory=list)
    default_value: str = ""
    class_name: str = ""
    line_number: int = 0


@dataclass
class UFunctionInfo:
    """Information about a UFUNCTION declaration."""
    name: str
    return_type: str
    parameters: list[str] = field(default_factory=list)
    specifiers: list[str] = field(default_factory=list)
    class_name: str = ""
    line_number: int = 0
    is_virtual: bool = False
    is_override: bool = False
    is_const: bool = False


@dataclass
class UClassInfo:
    """Information about a UCLASS declaration."""
    name: str
    parent: str = ""
    specifiers: list[str] = field(default_factory=list)
    line_number: int = 0
    methods: list[str] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)


@dataclass
class CppFileInfo:
    """Parsed information from a C++ file."""
    path: str
    includes: list[str] = field(default_factory=list)
    classes: list[UClassInfo] = field(default_factory=list)
    functions: list[UFunctionInfo] = field(default_factory=list)
    properties: list[UPropertyInfo] = field(default_factory=list)
    line_count: int = 0


class CppParser:
    """
    Extract UE macros and basic structure from C++ files.

    Uses regex patterns to find UCLASS, UFUNCTION, UPROPERTY declarations
    and extract their specifiers, types, and relationships.
    """

    # Regex patterns for UE macros
    # UCLASS(Blueprintable, BlueprintType) class MYGAME_API UMyClass : public AActor
    UCLASS_PATTERN = re.compile(
        r'UCLASS\s*\(\s*([^)]*)\s*\)\s*'  # UCLASS(specifiers)
        r'class\s+(?:(\w+_API)\s+)?'       # class [MODULE_API]
        r'(\w+)'                            # ClassName
        r'(?:\s*:\s*public\s+(\w+))?',      # [: public ParentClass]
        re.MULTILINE
    )

    # USTRUCT(BlueprintType) struct FMyStruct
    USTRUCT_PATTERN = re.compile(
        r'USTRUCT\s*\(\s*([^)]*)\s*\)\s*'
        r'struct\s+(?:(\w+_API)\s+)?'
        r'(\w+)',
        re.MULTILINE
    )

    # UFUNCTION(BlueprintCallable) void MyFunction(int32 Param);
    UFUNCTION_PATTERN = re.compile(
        r'UFUNCTION\s*\(\s*([^)]*)\s*\)\s*'  # UFUNCTION(specifiers)
        r'(?:virtual\s+)?'                   # [virtual]
        r'([\w:<>,\s\*&]+?)\s+'              # ReturnType
        r'(\w+)\s*'                          # FunctionName
        r'\(\s*([^)]*)\s*\)'                 # (Parameters)
        r'(?:\s*const)?'                     # [const]
        r'(?:\s*override)?',                 # [override]
        re.MULTILINE | re.DOTALL
    )

    # UPROPERTY(EditAnywhere, BlueprintReadWrite) float Health = 100.f;
    UPROPERTY_PATTERN = re.compile(
        r'UPROPERTY\s*\(\s*([^)]*)\s*\)\s*'  # UPROPERTY(specifiers)
        r'([\w:<>,\s\*&]+?)\s+'              # Type (including templates)
        r'(\w+)'                              # PropertyName
        r'(?:\s*=\s*([^;]+))?'               # [= DefaultValue]
        r'\s*;',                              # ;
        re.MULTILINE
    )

    # #include "MyHeader.h" or #include <system>
    INCLUDE_PATTERN = re.compile(
        r'#include\s+[<"]([^>"]+)[>"]',
        re.MULTILINE
    )

    # Regular class (non-UCLASS)
    CLASS_PATTERN = re.compile(
        r'^class\s+(?:(\w+_API)\s+)?'
        r'(\w+)'
        r'(?:\s*:\s*public\s+(\w+))?',
        re.MULTILINE
    )

    # Regular function declaration in header
    FUNCTION_PATTERN = re.compile(
        r'^\s*(?:virtual\s+)?'
        r'([\w:<>,\s\*&]+?)\s+'
        r'(\w+)\s*'
        r'\(\s*([^)]*)\s*\)'
        r'(?:\s*const)?'
        r'(?:\s*override)?'
        r'\s*;',
        re.MULTILINE
    )

    def parse_file(self, file_path: Path) -> CppFileInfo:
        """
        Parse a single .cpp or .h file.

        Args:
            file_path: Path to the C++ file

        Returns:
            CppFileInfo with extracted information
        """
        try:
            content = file_path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            return CppFileInfo(path=str(file_path))

        # Remove single-line comments for cleaner parsing
        content_no_comments = re.sub(r'//.*$', '', content, flags=re.MULTILINE)

        # Remove multi-line comments
        content_no_comments = re.sub(r'/\*.*?\*/', '', content_no_comments, flags=re.DOTALL)

        info = CppFileInfo(
            path=str(file_path),
            line_count=content.count('\n') + 1,
        )

        # Extract includes
        info.includes = self._extract_includes(content)

        # Extract UCLASS declarations
        info.classes = self._extract_uclasses(content_no_comments, content)

        # Extract UFUNCTION declarations
        info.functions = self._extract_ufunctions(content_no_comments, content)

        # Extract UPROPERTY declarations
        info.properties = self._extract_uproperties(content_no_comments, content)

        # Associate properties and functions with their classes
        self._associate_members(info, content_no_comments)

        return info

    def _extract_includes(self, content: str) -> list[str]:
        """Extract #include statements."""
        includes = []
        for match in self.INCLUDE_PATTERN.finditer(content):
            include = match.group(1)
            includes.append(include)
        return includes

    def _extract_uclasses(self, content: str, original_content: str) -> list[UClassInfo]:
        """Extract UCLASS declarations with specifiers."""
        classes = []

        for match in self.UCLASS_PATTERN.finditer(content):
            specifiers_str = match.group(1)
            class_name = match.group(3)
            parent_class = match.group(4) or ""

            # Parse specifiers
            specifiers = self._parse_specifiers(specifiers_str)

            # Calculate line number
            line_number = original_content[:match.start()].count('\n') + 1

            classes.append(UClassInfo(
                name=class_name,
                parent=parent_class,
                specifiers=specifiers,
                line_number=line_number,
            ))

        # Also check for USTRUCT
        for match in self.USTRUCT_PATTERN.finditer(content):
            specifiers_str = match.group(1)
            struct_name = match.group(3)

            specifiers = self._parse_specifiers(specifiers_str)
            line_number = original_content[:match.start()].count('\n') + 1

            classes.append(UClassInfo(
                name=struct_name,
                specifiers=specifiers,
                line_number=line_number,
            ))

        return classes

    def _extract_ufunctions(self, content: str, original_content: str) -> list[UFunctionInfo]:
        """Extract UFUNCTION declarations."""
        functions = []

        for match in self.UFUNCTION_PATTERN.finditer(content):
            specifiers_str = match.group(1)
            return_type = match.group(2).strip()
            func_name = match.group(3)
            params_str = match.group(4)

            # Parse specifiers
            specifiers = self._parse_specifiers(specifiers_str)

            # Parse parameters
            parameters = self._parse_parameters(params_str)

            # Calculate line number
            line_number = original_content[:match.start()].count('\n') + 1

            # Check for modifiers
            match_text = match.group(0)
            is_virtual = 'virtual' in match_text
            is_override = 'override' in match_text
            is_const = match_text.rstrip().endswith('const') or 'const override' in match_text

            functions.append(UFunctionInfo(
                name=func_name,
                return_type=return_type,
                parameters=parameters,
                specifiers=specifiers,
                line_number=line_number,
                is_virtual=is_virtual,
                is_override=is_override,
                is_const=is_const,
            ))

        return functions

    def _extract_uproperties(self, content: str, original_content: str) -> list[UPropertyInfo]:
        """Extract UPROPERTY declarations."""
        properties = []

        for match in self.UPROPERTY_PATTERN.finditer(content):
            specifiers_str = match.group(1)
            prop_type = match.group(2).strip()
            prop_name = match.group(3)
            default_value = (match.group(4) or "").strip()

            # Parse specifiers
            specifiers = self._parse_specifiers(specifiers_str)

            # Calculate line number
            line_number = original_content[:match.start()].count('\n') + 1

            properties.append(UPropertyInfo(
                name=prop_name,
                type=prop_type,
                specifiers=specifiers,
                default_value=default_value,
                line_number=line_number,
            ))

        return properties

    def _associate_members(self, info: CppFileInfo, content: str) -> None:
        """Associate functions and properties with their owning classes."""
        if not info.classes:
            return

        # Sort classes by line number
        sorted_classes = sorted(info.classes, key=lambda c: c.line_number)

        # For each function/property, find the class it belongs to
        for func in info.functions:
            owner = self._find_owner_class(func.line_number, sorted_classes)
            if owner:
                func.class_name = owner.name
                if func.name not in owner.methods:
                    owner.methods.append(func.name)

        for prop in info.properties:
            owner = self._find_owner_class(prop.line_number, sorted_classes)
            if owner:
                prop.class_name = owner.name
                if prop.name not in owner.properties:
                    owner.properties.append(prop.name)

    def _find_owner_class(self, line_number: int, sorted_classes: list[UClassInfo]) -> Optional[UClassInfo]:
        """Find the class that owns a member at the given line number."""
        owner = None
        for cls in sorted_classes:
            if cls.line_number < line_number:
                owner = cls
            else:
                break
        return owner

    def _parse_specifiers(self, specifiers_str: str) -> list[str]:
        """Parse comma-separated specifiers, handling nested parentheses."""
        if not specifiers_str or not specifiers_str.strip():
            return []

        specifiers = []
        current = ""
        depth = 0

        for char in specifiers_str:
            if char == '(':
                depth += 1
                current += char
            elif char == ')':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                spec = current.strip()
                if spec:
                    specifiers.append(spec)
                current = ""
            else:
                current += char

        # Don't forget the last one
        spec = current.strip()
        if spec:
            specifiers.append(spec)

        return specifiers

    def _parse_parameters(self, params_str: str) -> list[str]:
        """Parse function parameters."""
        if not params_str or not params_str.strip():
            return []

        params = []
        current = ""
        depth = 0

        for char in params_str:
            if char in '(<':
                depth += 1
                current += char
            elif char in ')>':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                param = current.strip()
                if param:
                    params.append(param)
                current = ""
            else:
                current += char

        # Don't forget the last one
        param = current.strip()
        if param:
            params.append(param)

        return params

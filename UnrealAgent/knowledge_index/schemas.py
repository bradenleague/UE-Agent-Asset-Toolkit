"""
Document chunk schemas for the semantic knowledge index.

Each chunk type represents a specific kind of indexed content:
- asset_summary: High-level asset information
- umg_widget_tree: Widget hierarchy from WidgetBlueprints
- bp_graph_summary: Blueprint function information
- material_params: Material/MaterialInstance parameters
- cpp_symbol: C++ class/function definitions (Phase 2)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import hashlib
import json

from UnrealAgent.pathutil import to_game_path_sep


def extract_module_from_asset_path(path: str) -> str:
    """Extract module name from asset path (e.g., /Game/UI/HUD/Widget -> UI)."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "Game":
        return parts[2]
    return "Unknown"


def extract_module_from_source_path(path: str) -> str:
    """Extract module name from source path (e.g., Source/MyGame/Public/Foo.h -> MyGame)."""
    parts = to_game_path_sep(path).split("/")
    if len(parts) >= 2 and parts[0] == "Source":
        return parts[1]
    if len(parts) >= 2 and parts[0] == "Plugins":
        return parts[1]
    return "Unknown"


@dataclass
class DocChunk:
    """Base document chunk with common fields."""

    doc_id: str
    type: str
    path: str
    name: str
    text: str
    metadata: dict = field(default_factory=dict)
    references_out: list[str] = field(default_factory=list)
    typed_references_out: dict[str, str] = field(default_factory=dict)
    module: Optional[str] = None
    asset_type: Optional[str] = None
    fingerprint: Optional[str] = None
    schema_version: int = 1
    embed_model: Optional[str] = None
    embed_version: Optional[str] = None
    indexed_at: Optional[datetime] = None
    embedding: Optional[list[float]] = None

    def __post_init__(self):
        if self.fingerprint is None:
            self.fingerprint = self.compute_fingerprint()

    def compute_fingerprint(self) -> str:
        """Compute SHA256 fingerprint of text content for change detection."""
        normalized = self.text.strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "doc_id": self.doc_id,
            "type": self.type,
            "path": self.path,
            "name": self.name,
            "text": self.text,
            "metadata": json.dumps(self.metadata) if self.metadata else "{}",
            "references_out": json.dumps(self.references_out)
            if self.references_out
            else "[]",
            "module": self.module,
            "asset_type": self.asset_type,
            "fingerprint": self.fingerprint,
            "schema_version": self.schema_version,
            "embed_model": self.embed_model,
            "embed_version": self.embed_version,
            "indexed_at": self.indexed_at.isoformat() if self.indexed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DocChunk":
        """Create from dictionary."""
        metadata = data.get("metadata", "{}")
        if isinstance(metadata, str):
            metadata = json.loads(metadata)

        refs = data.get("references_out", "[]")
        if isinstance(refs, str):
            refs = json.loads(refs)

        indexed_at = data.get("indexed_at")
        if isinstance(indexed_at, str):
            indexed_at = datetime.fromisoformat(indexed_at)

        return cls(
            doc_id=data["doc_id"],
            type=data["type"],
            path=data["path"],
            name=data["name"],
            text=data["text"],
            metadata=metadata,
            references_out=refs,
            module=data.get("module"),
            asset_type=data.get("asset_type"),
            fingerprint=data.get("fingerprint"),
            schema_version=data.get("schema_version", 1),
            embed_model=data.get("embed_model"),
            embed_version=data.get("embed_version"),
            indexed_at=indexed_at,
        )


@dataclass
class AssetSummary(DocChunk):
    """
    High-level asset summary. One per .uasset file.

    doc_id format: asset:{path}
    Example: asset:/Game/UI/HUD/WBP_Reticle
    """

    def __init__(
        self,
        path: str,
        name: str,
        asset_type: str,
        widget_count: int = 0,
        function_count: int = 0,
        parent_class: str = "",
        events: list[str] = None,
        functions: list[str] = None,
        components: list[str] = None,
        variables: list[str] = None,
        interfaces: list[str] = None,
        references_out: list[str] = None,
        module: str = None,
    ):
        events = events or []
        functions = functions or []
        components = components or []
        variables = variables or []
        interfaces = interfaces or []
        references_out = references_out or []

        # Generate readable text summary (expanded for better search, 67 -> 300+ chars)
        text_parts = [f"{name} is a {asset_type}"]

        if parent_class:
            text_parts.append(f"inheriting from {parent_class}")

        if interfaces:
            text_parts.append(f"implementing {', '.join(interfaces[:5])}")

        if widget_count > 0:
            text_parts.append(f"containing {widget_count} widgets")

        # Include more items for better searchability (10 instead of 5)
        if components:
            comp_list = ", ".join(components[:10])
            text_parts.append(f"Components: {comp_list}")

        if events:
            event_list = ", ".join(events[:10])
            text_parts.append(f"Events: {event_list}")

        if functions:
            func_list = ", ".join(functions[:10])
            text_parts.append(f"Functions: {func_list}")

        if variables:
            var_list = ", ".join(variables[:10])
            text_parts.append(f"Variables: {var_list}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "widget_count": widget_count,
            "function_count": function_count,
            "parent_class": parent_class,
            "events": events[:15],
            "functions": functions[:15],
            "components": components[:15],
            "variables": variables[:15],
            "interfaces": interfaces[:10],
        }

        super().__init__(
            doc_id=f"asset:{path}",
            type="asset_summary",
            path=path,
            name=name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_asset_path(path),
            asset_type=asset_type,
        )


@dataclass
class WidgetTreeDoc(DocChunk):
    """
    Widget hierarchy from a WidgetBlueprint.

    doc_id format: widget:{path}/WidgetTree
    Example: widget:/Game/UI/HUD/WBP_Reticle/WidgetTree
    """

    def __init__(
        self,
        path: str,
        name: str,
        root_widget: str,
        widget_names: list[str],
        widget_hierarchy: str,  # Text representation of hierarchy
        references_out: list[str] = None,
        module: str = None,
    ):
        references_out = references_out or []

        # Generate readable text
        text = (
            f"Widget tree for {name}. Root widget: {root_widget}. "
            f"Contains widgets: {', '.join(widget_names[:15])}. "
            f"Hierarchy: {widget_hierarchy}"
        )

        metadata = {
            "root_widget": root_widget,
            "widget_names": widget_names,
            "widget_count": len(widget_names),
        }

        super().__init__(
            doc_id=f"widget:{path}/WidgetTree",
            type="umg_widget_tree",
            path=path,
            name=f"{name}/WidgetTree",
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_asset_path(path),
            asset_type="WidgetBlueprint",
        )


@dataclass
class BlueprintGraphDoc(DocChunk):
    """
    Blueprint function graph information.

    doc_id format: bp_func:{path}::{function_name}
    Example: bp_func:/Game/UI/HUD/WBP_Reticle::UpdateReticle
    """

    def __init__(
        self,
        path: str,
        asset_name: str,
        function_name: str,
        flags: list[str],
        calls: list[str],
        variables: list[str],
        references_out: list[str] = None,
        module: str = None,
        is_event: bool = False,
        control_flow: dict = None,
        parameters: list[dict] = None,
    ):
        references_out = references_out or []
        control_flow = control_flow or {}
        parameters = parameters or []

        func_type = "Event" if is_event else "Function"

        # Generate readable text
        text_parts = [f"{func_type} {function_name} in {asset_name}"]

        if flags:
            text_parts.append(f"Flags: {', '.join(flags)}")

        if parameters:
            dir_prefix = {"in": "", "out": "out ", "return": "returns "}
            param_strs = [
                f"{dir_prefix.get(p.get('direction', 'in'), '')}{p.get('name', '')}: {p.get('type', '')}"
                for p in parameters
            ]
            text_parts.append(f"Parameters: {', '.join(param_strs)}")

        if calls:
            text_parts.append(f"Calls: {', '.join(calls[:10])}")

        if variables:
            text_parts.append(f"Variables: {', '.join(variables[:10])}")

        # Add control flow description for searchability
        if control_flow.get("has_branches"):
            complexity = control_flow.get("complexity", "unknown")
            text_parts.append(f"Contains conditional logic ({complexity} complexity)")

        text = ". ".join(text_parts) + "."

        metadata = {
            "flags": flags,
            "calls": calls,
            "variables": variables,
            "is_event": is_event,
            "control_flow": control_flow,
            "parameters": parameters,
        }

        super().__init__(
            doc_id=f"bp_func:{path}::{function_name}",
            type="bp_graph_summary",
            path=path,
            name=function_name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_asset_path(path),
            asset_type="Blueprint",
        )


@dataclass
class MaterialParamsDoc(DocChunk):
    """
    Material or MaterialInstance parameters.

    doc_id format: material:{path}
    Example: material:/Game/UI/Materials/MI_Reticle_Dynamic
    """

    def __init__(
        self,
        path: str,
        name: str,
        is_instance: bool,
        parent: str = "",
        domain: str = "Surface",
        blend_mode: str = "Opaque",
        shading_model: str = "DefaultLit",
        scalar_params: dict = None,
        vector_params: dict = None,
        texture_params: dict = None,
        static_switches: dict = None,
        references_out: list[str] = None,
        module: str = None,
    ):
        scalar_params = scalar_params or {}
        vector_params = vector_params or {}
        texture_params = texture_params or {}
        static_switches = static_switches or {}
        references_out = references_out or []

        mat_type = "MaterialInstance" if is_instance else "Material"

        # Generate readable text
        text_parts = [f"{mat_type} {name}"]

        if parent:
            text_parts.append(f"inherits from {parent}")

        text_parts.append(
            f"Domain: {domain}, Blend: {blend_mode}, Shading: {shading_model}"
        )

        if scalar_params:
            params = ", ".join(f"{k}={v}" for k, v in list(scalar_params.items())[:5])
            text_parts.append(f"Scalar params: {params}")

        if vector_params:
            params = ", ".join(f"{k}" for k in list(vector_params.keys())[:5])
            text_parts.append(f"Vector params: {params}")

        if texture_params:
            params = ", ".join(f"{k}={v}" for k, v in list(texture_params.items())[:5])
            text_parts.append(f"Texture params: {params}")

        if static_switches:
            params = ", ".join(f"{k}={v}" for k, v in list(static_switches.items())[:5])
            text_parts.append(f"Static switches: {params}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "is_instance": is_instance,
            "parent": parent,
            "domain": domain,
            "blend_mode": blend_mode,
            "shading_model": shading_model,
            "scalar_params": scalar_params,
            "vector_params": vector_params,
            "texture_params": texture_params,
            "static_switches": static_switches,
        }

        # Add parent to references
        if parent and parent not in references_out:
            references_out = [parent] + references_out

        # Add textures to references
        for tex_path in texture_params.values():
            if tex_path and tex_path not in references_out and tex_path.startswith("/"):
                references_out.append(tex_path)

        super().__init__(
            doc_id=f"material:{path}",
            type="material_params",
            path=path,
            name=name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_asset_path(path),
            asset_type="MaterialInstance" if is_instance else "Material",
        )


@dataclass
class MaterialFunctionDoc(DocChunk):
    """
    MaterialFunction inputs, outputs, and parameters.

    doc_id format: materialfunction:{path}
    Example: materialfunction:/Game/Materials/MFunc/MF_EdgeWear
    """

    def __init__(
        self,
        path: str,
        name: str,
        inputs: list[dict] = None,  # [{name, type, priority}]
        outputs: list[dict] = None,  # [{name, priority}]
        scalar_params: dict = None,
        vector_params: dict = None,
        static_switches: dict = None,
        references_out: list[str] = None,
        module: str = None,
    ):
        inputs = inputs or []
        outputs = outputs or []
        scalar_params = scalar_params or {}
        vector_params = vector_params or {}
        static_switches = static_switches or {}
        references_out = references_out or []

        # Generate readable text
        text_parts = [f"MaterialFunction {name}"]

        if inputs:
            input_desc = ", ".join(f"{i['name']}({i['type']})" for i in inputs[:5])
            text_parts.append(f"Inputs: {input_desc}")

        if outputs:
            output_desc = ", ".join(o["name"] for o in outputs[:5])
            text_parts.append(f"Outputs: {output_desc}")

        if scalar_params:
            params = ", ".join(f"{k}={v}" for k, v in list(scalar_params.items())[:5])
            text_parts.append(f"Scalar params: {params}")

        if vector_params:
            params = ", ".join(f"{k}" for k in list(vector_params.keys())[:5])
            text_parts.append(f"Vector params: {params}")

        if static_switches:
            params = ", ".join(f"{k}={v}" for k, v in list(static_switches.items())[:5])
            text_parts.append(f"Static switches: {params}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "inputs": inputs,
            "outputs": outputs,
            "scalar_params": scalar_params,
            "vector_params": vector_params,
            "static_switches": static_switches,
            "input_count": len(inputs),
            "output_count": len(outputs),
            "param_count": len(scalar_params)
            + len(vector_params)
            + len(static_switches),
        }

        super().__init__(
            doc_id=f"materialfunction:{path}",
            type="materialfunction_params",
            path=path,
            name=name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_asset_path(path),
            asset_type="MaterialFunction",
        )


# =============================================================================
# C++ Source Document Types
# =============================================================================


@dataclass
class SourceFileDoc(DocChunk):
    """
    Summary of a C++ source file (.cpp or .h).

    doc_id format: source:{relative_path}
    Example: source:Source/MyGame/MyCharacter.h
    """

    def __init__(
        self,
        path: str,
        name: str,
        line_count: int = 0,
        includes: list[str] = None,
        class_count: int = 0,
        function_count: int = 0,
        property_count: int = 0,
        references_out: list[str] = None,
        module: str = None,
    ):
        includes = includes or []
        references_out = references_out or []

        # Generate readable text summary
        text_parts = [
            f"{name} is a {'header' if name.endswith('.h') else 'source'} file"
        ]

        if line_count > 0:
            text_parts.append(f"with {line_count} lines")

        if class_count > 0:
            text_parts.append(
                f"containing {class_count} class{'es' if class_count > 1 else ''}"
            )

        if function_count > 0:
            text_parts.append(
                f"{function_count} function{'s' if function_count > 1 else ''}"
            )

        if property_count > 0:
            text_parts.append(
                f"{property_count} propert{'ies' if property_count > 1 else 'y'}"
            )

        if includes:
            text_parts.append(f"Includes: {', '.join(includes[:5])}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "line_count": line_count,
            "includes": includes[:20],
            "class_count": class_count,
            "function_count": function_count,
            "property_count": property_count,
        }

        super().__init__(
            doc_id=f"source:{path}",
            type="source_file",
            path=path,
            name=name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_source_path(path),
            asset_type="SourceFile",
        )


@dataclass
class CppClassDoc(DocChunk):
    """
    A UCLASS or regular C++ class.

    doc_id format: cpp_class:{path}::{class_name}
    Example: cpp_class:Source/MyGame/MyCharacter.h::UMyCharacter
    """

    def __init__(
        self,
        path: str,
        class_name: str,
        parent_class: str = "",
        specifiers: list[str] = None,
        methods: list[str] = None,
        properties: list[str] = None,
        is_uclass: bool = False,
        line_number: int = 0,
        references_out: list[str] = None,
        module: str = None,
    ):
        specifiers = specifiers or []
        methods = methods or []
        properties = properties or []
        references_out = references_out or []

        # Generate readable text
        class_type = "UCLASS" if is_uclass else "class"
        text_parts = [f"{class_name} is a {class_type}"]

        if parent_class:
            text_parts.append(f"inheriting from {parent_class}")

        if specifiers:
            text_parts.append(f"with specifiers: {', '.join(specifiers[:5])}")

        if methods:
            text_parts.append(f"Methods: {', '.join(methods[:10])}")

        if properties:
            text_parts.append(f"Properties: {', '.join(properties[:10])}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "parent_class": parent_class,
            "specifiers": specifiers,
            "methods": methods[:20],
            "properties": properties[:20],
            "is_uclass": is_uclass,
            "line_number": line_number,
        }

        # Add parent class to references
        if parent_class and parent_class not in references_out:
            references_out = [parent_class] + references_out

        super().__init__(
            doc_id=f"cpp_class:{path}::{class_name}",
            type="cpp_class",
            path=path,
            name=class_name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_source_path(path),
            asset_type="CppClass",
        )


@dataclass
class CppFunctionDoc(DocChunk):
    """
    A UFUNCTION or regular C++ function.

    doc_id format: cpp_func:{path}::{class_name}::{func_name} or cpp_func:{path}::{func_name}
    Example: cpp_func:Source/MyGame/MyCharacter.h::UMyCharacter::TakeDamage
    """

    def __init__(
        self,
        path: str,
        function_name: str,
        return_type: str = "void",
        parameters: list[str] = None,
        specifiers: list[str] = None,
        class_name: str = "",
        is_ufunction: bool = False,
        line_number: int = 0,
        references_out: list[str] = None,
        module: str = None,
    ):
        parameters = parameters or []
        specifiers = specifiers or []
        references_out = references_out or []

        # Build doc_id
        if class_name:
            doc_id = f"cpp_func:{path}::{class_name}::{function_name}"
        else:
            doc_id = f"cpp_func:{path}::{function_name}"

        # Generate readable text
        func_type = "UFUNCTION" if is_ufunction else "function"
        text_parts = [f"{function_name} is a {func_type}"]

        if return_type and return_type != "void":
            text_parts.append(f"returning {return_type}")

        if parameters:
            text_parts.append(f"Parameters: {', '.join(parameters[:5])}")

        if specifiers:
            text_parts.append(f"Specifiers: {', '.join(specifiers[:5])}")

        if class_name:
            text_parts.append(f"in class {class_name}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "return_type": return_type,
            "parameters": parameters,
            "specifiers": specifiers,
            "class_name": class_name,
            "is_ufunction": is_ufunction,
            "line_number": line_number,
        }

        super().__init__(
            doc_id=doc_id,
            type="cpp_func",
            path=path,
            name=function_name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_source_path(path),
            asset_type="CppFunction",
        )


@dataclass
class CppPropertyDoc(DocChunk):
    """
    A UPROPERTY member variable.

    doc_id format: cpp_prop:{path}::{class_name}::{prop_name}
    Example: cpp_prop:Source/MyGame/MyCharacter.h::UMyCharacter::Health
    """

    def __init__(
        self,
        path: str,
        property_name: str,
        property_type: str = "",
        specifiers: list[str] = None,
        default_value: str = "",
        class_name: str = "",
        line_number: int = 0,
        references_out: list[str] = None,
        module: str = None,
    ):
        specifiers = specifiers or []
        references_out = references_out or []

        # Generate readable text
        text_parts = [f"{property_name} is a UPROPERTY"]

        if property_type:
            text_parts.append(f"of type {property_type}")

        if specifiers:
            text_parts.append(f"with specifiers: {', '.join(specifiers[:5])}")

        if default_value:
            text_parts.append(f"default value: {default_value}")

        if class_name:
            text_parts.append(f"in class {class_name}")

        text = ". ".join(text_parts) + "."

        metadata = {
            "type": property_type,
            "specifiers": specifiers,
            "default_value": default_value,
            "class_name": class_name,
            "line_number": line_number,
        }

        super().__init__(
            doc_id=f"cpp_prop:{path}::{class_name}::{property_name}",
            type="cpp_property",
            path=path,
            name=property_name,
            text=text,
            metadata=metadata,
            references_out=references_out,
            module=module or extract_module_from_source_path(path),
            asset_type="CppProperty",
        )


@dataclass
class SearchResult:
    """Result from semantic or exact search."""

    doc_id: str
    score: float
    doc: Optional[DocChunk] = None
    highlight: Optional[str] = None  # Highlighted snippet

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "score": self.score,
            "doc": self.doc.to_dict() if self.doc else None,
            "highlight": self.highlight,
        }


@dataclass
class ReferenceGraph:
    """Graph of document references."""

    seed_id: str
    forward_refs: dict[str, list[str]]  # doc_id -> [referenced_doc_ids]
    reverse_refs: dict[str, list[str]]  # doc_id -> [referencing_doc_ids]
    nodes: dict[str, DocChunk]  # doc_id -> DocChunk (for included nodes)
    depth: int

    def to_dict(self) -> dict:
        return {
            "seed_id": self.seed_id,
            "forward_refs": self.forward_refs,
            "reverse_refs": self.reverse_refs,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "depth": self.depth,
        }


@dataclass
class IndexStatus:
    """Status of the knowledge index."""

    total_docs: int
    docs_by_type: dict[str, int]
    total_edges: int
    last_indexed: Optional[datetime]
    pending_updates: int
    embed_model: Optional[str]
    schema_version: int
    # Lightweight assets (path + refs only, no embeddings)
    lightweight_total: int = 0
    lightweight_by_type: dict = None

    def __post_init__(self):
        if self.lightweight_by_type is None:
            self.lightweight_by_type = {}

    def to_dict(self) -> dict:
        return {
            "total_docs": self.total_docs,
            "docs_by_type": self.docs_by_type,
            "total_edges": self.total_edges,
            "last_indexed": self.last_indexed.isoformat()
            if self.last_indexed
            else None,
            "pending_updates": self.pending_updates,
            "embed_model": self.embed_model,
            "schema_version": self.schema_version,
            "lightweight_total": self.lightweight_total,
            "lightweight_by_type": self.lightweight_by_type,
        }

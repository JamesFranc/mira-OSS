"""
Domain Document Tool - Section-aware editing with version control.

Provides section-level management for domain knowledge documents stored in SQLite.
Operations are section-scoped with expand/collapse support and full version history.
"""
import json
import logging
from typing import Dict, Any, Optional, List, TYPE_CHECKING

from pydantic import BaseModel, Field
from tools.repo import Tool
from tools.registry import registry
from utils.timezone_utils import utc_now, format_utc_iso
from utils.userdata_manager import UserDataManager

if TYPE_CHECKING:
    from working_memory.core import WorkingMemory

logger = logging.getLogger(__name__)


class DomaindocToolConfig(BaseModel):
    """Configuration for the domaindoc tool."""
    enabled: bool = Field(default=True, description="Enable/disable the domaindoc tool")


registry.register("domaindoc_tool", DomaindocToolConfig)


class DomaindocTool(Tool):
    """Section-aware editing tool for domain knowledge documents."""

    name = "domaindoc_tool"

    simple_description = """Section-aware editing for domain knowledge documents with expand/collapse support."""

    anthropic_schema = {
        "name": "domaindoc_tool",
        "description": """Edit domain knowledge documents with section-level control and one level of nesting.

CRITICAL PARAMETER NAMES (use these exact names - do not use alternatives):
• label (NOT "domain" or "name") - Identifies which domaindoc to edit
• find and replace (NOT "old_str" or "new_str") - For sed/sed_all operations
• section (NOT "header") - Section header to target
• content (NOT "text" or "body") - Content to add/replace

IMPORTANT: Collapsed sections show ONLY headers - content is hidden until expanded.
First section is always expanded (overview). When a parent section is collapsed, ALL its subsections are hidden.

SUBSECTIONS: Use parent="ParentName" to work with subsections.
• Top-level section: section="Research" (no parent)
• Subsection: section="Competitors", parent="Research"
Depth limit is 1 - subsections cannot have children. OVERVIEW section cannot have subsections.

SECTION MANAGEMENT:
• expand/collapse - section="NAME" (add parent="X" for subsections), or sections=["A","B"] for batch.
• set_expanded_by_default - section="NAME", expanded_by_default=true/false. Marks section to show expanded by default (still collapsible, unlike overview).
• create_section - section="HEADER", content="...", optional parent="X", optional expanded_by_default=true.
• rename_section - section="OLD", new_name="NEW".
• delete_section - Remove section (must be expanded; if parent, all subsections must be expanded).
• reorder_sections - order=["ALL","SECTIONS","IN","ORDER"] (add parent="X" to reorder subsections).

CONTENT EDITING (all require section, add parent for subsections):
• append - section="NAME", content="new text"
• sed/sed_all - section="NAME", find="old", replace="new"
• replace_section - section="NAME", content="new content" """,
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "expand", "collapse", "set_expanded_by_default",
                        "create_section", "rename_section",
                        "delete_section", "reorder_sections",
                        "append", "sed", "sed_all", "replace_section"
                    ],
                    "description": "Operation to perform"
                },
                "label": {
                    "type": "string",
                    "description": "Domaindoc to edit (e.g., 'marketing_plan')"
                },
                "section": {
                    "type": "string",
                    "description": "Section header (exact match required)"
                },
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Section headers for batch expand/collapse"
                },
                "parent": {
                    "type": "string",
                    "description": "Parent section header when targeting a subsection"
                },
                "content": {
                    "type": "string",
                    "description": "Content for append/create_section/replace_section"
                },
                "find": {
                    "type": "string",
                    "description": "Text to find (sed/sed_all)"
                },
                "replace": {
                    "type": "string",
                    "description": "Replacement text (sed/sed_all)"
                },
                "new_name": {
                    "type": "string",
                    "description": "New header (rename_section)"
                },
                "after": {
                    "type": "string",
                    "description": "Insert after this section (create_section)"
                },
                "expanded_by_default": {
                    "type": "boolean",
                    "description": "Mark section as expanded by default (create_section, set_expanded_by_default). Unlike overview, these sections can still be collapsed."
                },
                "order": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "All section headers in new order (reorder_sections)"
                }
            },
            "required": ["operation", "label"]
        }
    }

    # =========================================================================
    # Database Helpers
    # =========================================================================

    def _normalize_section_name(self, name: str) -> str:
        """Strip ` | alert` suffix that may be included from trinket display."""
        if ' | ' in name:
            return name.split(' | ')[0].strip()
        return name.strip()

    def _get_domaindoc(self, db: UserDataManager, label: str) -> Dict[str, Any]:
        """Get domaindoc by label, raising ValueError if not found or disabled."""
        results = db.select("domaindocs", "label = :label", {"label": label})
        if not results:
            raise ValueError(f"Domaindoc '{label}' not found")
        doc = results[0]
        if not doc.get("enabled", True):
            raise ValueError(f"Domaindoc '{label}' is not enabled")
        return doc

    def _get_section(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        header: str,
        parent_header: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get section by header, optionally under a parent. Raises ValueError if not found."""
        normalized = self._normalize_section_name(header)

        if parent_header:
            # Get parent first, then find child under it
            parent = self._get_section(db, domaindoc_id, parent_header)
            results = db.fetchall(
                "SELECT * FROM domaindoc_sections WHERE domaindoc_id = :doc_id AND header = :header AND parent_section_id = :parent_id",
                {"doc_id": domaindoc_id, "header": normalized, "parent_id": parent["id"]}
            )
            if not results:
                raise ValueError(f"Subsection '{header}' not found under '{parent_header}'")
        else:
            # Top-level section (no parent)
            results = db.fetchall(
                "SELECT * FROM domaindoc_sections WHERE domaindoc_id = :doc_id AND header = :header AND parent_section_id IS NULL",
                {"doc_id": domaindoc_id, "header": normalized}
            )
            if not results:
                raise ValueError(f"Section '{header}' not found")

        return db._decrypt_dict(results[0])

    def _get_all_sections(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        parent_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get sections for a domaindoc, optionally filtered by parent. Ordered by sort_order."""
        if parent_id is not None:
            results = db.fetchall(
                "SELECT * FROM domaindoc_sections WHERE domaindoc_id = :doc_id AND parent_section_id = :parent_id ORDER BY sort_order",
                {"doc_id": domaindoc_id, "parent_id": parent_id}
            )
        else:
            # Get top-level sections only (parent_section_id IS NULL)
            results = db.fetchall(
                "SELECT * FROM domaindoc_sections WHERE domaindoc_id = :doc_id AND parent_section_id IS NULL ORDER BY sort_order",
                {"doc_id": domaindoc_id}
            )
        return [db._decrypt_dict(row) for row in results]

    def _get_subsections(self, db: UserDataManager, parent_id: int) -> List[Dict[str, Any]]:
        """Get all subsections of a parent section."""
        results = db.fetchall(
            "SELECT * FROM domaindoc_sections WHERE parent_section_id = :parent_id ORDER BY sort_order",
            {"parent_id": parent_id}
        )
        return [db._decrypt_dict(row) for row in results]

    def _count_subsections(self, db: UserDataManager, parent_id: int) -> int:
        """Count subsections of a parent section."""
        result = db.fetchone(
            "SELECT COUNT(*) as count FROM domaindoc_sections WHERE parent_section_id = :parent_id",
            {"parent_id": parent_id}
        )
        return result.get("count", 0) if result else 0

    def _record_version(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        operation: str,
        diff_data: Dict[str, Any],
        section_id: Optional[int] = None
    ) -> int:
        """Record a version entry. Calculates version_num atomically via subquery."""
        now = format_utc_iso(utc_now())

        db.execute(
            """
            INSERT INTO domaindoc_versions
                (domaindoc_id, section_id, version_num, operation, encrypted__diff_data, created_at)
            VALUES (
                :domaindoc_id,
                :section_id,
                (SELECT COALESCE(MAX(version_num), 0) + 1 FROM domaindoc_versions WHERE domaindoc_id = :domaindoc_id),
                :operation,
                :diff_data,
                :now
            )
            """,
            {
                "domaindoc_id": domaindoc_id,
                "section_id": section_id,
                "operation": operation,
                "diff_data": json.dumps(diff_data),
                "now": now
            }
        )

        result = db.fetchone(
            "SELECT MAX(version_num) as ver FROM domaindoc_versions WHERE domaindoc_id = :doc_id",
            {"doc_id": domaindoc_id}
        )
        return result.get("ver", 1)

    def _update_domaindoc_timestamp(self, db: UserDataManager, domaindoc_id: int) -> None:
        """Update the domaindoc's updated_at timestamp."""
        now = format_utc_iso(utc_now())
        db.execute(
            "UPDATE domaindocs SET updated_at = :now WHERE id = :doc_id",
            {"now": now, "doc_id": domaindoc_id}
        )

    # =========================================================================
    # Tool Interface
    # =========================================================================

    def is_available(self) -> bool:
        """Available when at least one domaindoc is enabled."""
        try:
            db = self.db  # Uses cached UserDataManager from Tool base class
            results = db.fetchall(
                "SELECT 1 FROM domaindocs WHERE enabled = TRUE LIMIT 1"
            )
            return len(results) > 0
        except RuntimeError:
            return False
        except Exception as e:
            logger.warning(f"Domaindoc availability check failed: {e}")
            return False

    def run(
        self,
        operation: str,
        label: str,
        section: Optional[str] = None,
        sections: Optional[List[str]] = None,
        content: Optional[str] = None,
        find: Optional[str] = None,
        replace: Optional[str] = None,
        new_name: Optional[str] = None,
        after: Optional[str] = None,
        order: Optional[List[str]] = None,
        parent: Optional[str] = None,
        expanded_by_default: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Execute an operation on a domaindoc. Use parent param to target subsections."""
        db = self.db  # Uses cached UserDataManager from Tool base class
        doc = self._get_domaindoc(db, label)
        domaindoc_id = doc["id"]

        if operation == "expand":
            return self._op_expand(db, domaindoc_id, section, sections, parent)
        elif operation == "collapse":
            return self._op_collapse(db, domaindoc_id, section, sections, parent)
        elif operation == "set_expanded_by_default":
            return self._op_set_expanded_by_default(db, domaindoc_id, section, sections, parent, expanded_by_default)
        elif operation == "create_section":
            return self._op_create_section(db, domaindoc_id, section, content, after, parent, expanded_by_default)
        elif operation == "rename_section":
            return self._op_rename_section(db, domaindoc_id, section, new_name, parent)
        elif operation == "delete_section":
            return self._op_delete_section(db, domaindoc_id, section, parent)
        elif operation == "reorder_sections":
            return self._op_reorder_sections(db, domaindoc_id, order, parent)
        elif operation == "append":
            return self._op_append(db, domaindoc_id, section, content, parent)
        elif operation == "sed":
            return self._op_sed(db, domaindoc_id, section, find, replace, global_replace=False, parent=parent)
        elif operation == "sed_all":
            return self._op_sed(db, domaindoc_id, section, find, replace, global_replace=True, parent=parent)
        elif operation == "replace_section":
            return self._op_replace_section(db, domaindoc_id, section, content, parent)
        else:
            raise ValueError(f"Unknown operation: {operation}")

    # =========================================================================
    # Section Management Operations
    # =========================================================================

    def _op_expand(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        sections: Optional[List[str]],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Expand one or more sections. Use parent param for subsections."""
        targets = self._resolve_section_targets(section, sections)
        if not targets:
            raise ValueError("expand requires 'section' or 'sections' parameter")

        expanded = []
        for header in targets:
            sec = self._get_section(db, domaindoc_id, header, parent)
            db.execute(
                "UPDATE domaindoc_sections SET collapsed = FALSE, updated_at = :now WHERE id = :id",
                {"now": format_utc_iso(utc_now()), "id": sec["id"]}
            )
            expanded.append(sec["header"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {"success": True, "expanded": expanded, "parent": parent}

    def _op_collapse(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        sections: Optional[List[str]],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Collapse one or more sections. First top-level section cannot be collapsed."""
        targets = self._resolve_section_targets(section, sections)
        if not targets:
            raise ValueError("collapse requires 'section' or 'sections' parameter")

        collapsed = []
        skipped = []
        for header in targets:
            sec = self._get_section(db, domaindoc_id, header, parent)
            # First top-level section cannot be collapsed (subsections can be)
            if sec["sort_order"] == 0 and sec.get("parent_section_id") is None:
                skipped.append(sec["header"])
                continue
            db.execute(
                "UPDATE domaindoc_sections SET collapsed = TRUE, updated_at = :now WHERE id = :id",
                {"now": format_utc_iso(utc_now()), "id": sec["id"]}
            )
            collapsed.append(sec["header"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        result = {"success": True, "collapsed": collapsed, "parent": parent}
        if skipped:
            result["skipped"] = skipped
            result["note"] = "First section cannot be collapsed (serves as overview)"
        return result

    def _op_set_expanded_by_default(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        sections: Optional[List[str]],
        parent: Optional[str] = None,
        expanded_by_default: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Set expanded_by_default flag on sections. Also expands them if setting to True."""
        targets = self._resolve_section_targets(section, sections)
        if not targets:
            raise ValueError("set_expanded_by_default requires 'section' or 'sections' parameter")
        if expanded_by_default is None:
            raise ValueError("set_expanded_by_default requires 'expanded_by_default' parameter (true/false)")

        updated = []
        skipped = []
        for header in targets:
            sec = self._get_section(db, domaindoc_id, header, parent)
            # First top-level section is always expanded - skip setting flag
            if sec["sort_order"] == 0 and sec.get("parent_section_id") is None:
                skipped.append(sec["header"])
                continue

            # Update flag and also set collapsed state to match
            db.execute(
                "UPDATE domaindoc_sections SET expanded_by_default = :flag, collapsed = :collapsed, updated_at = :now WHERE id = :id",
                {
                    "flag": expanded_by_default,
                    "collapsed": not expanded_by_default,  # expanded_by_default=True means collapsed=False
                    "now": format_utc_iso(utc_now()),
                    "id": sec["id"]
                }
            )
            updated.append(sec["header"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        result = {"success": True, "updated": updated, "expanded_by_default": expanded_by_default, "parent": parent}
        if skipped:
            result["skipped"] = skipped
            result["note"] = "First section is always expanded (overview)"
        return result

    def _op_create_section(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        content: Optional[str],
        after: Optional[str],
        parent: Optional[str] = None,
        expanded_by_default: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Create a new section or subsection. Use parent param to create subsection."""
        if not section:
            raise ValueError("create_section requires 'section' parameter (the new header)")
        if content is None:
            raise ValueError("create_section requires 'content' parameter")

        header = self._normalize_section_name(section)
        now = format_utc_iso(utc_now())
        parent_section_id = None

        if parent:
            # Creating a subsection - validate parent exists and is top-level
            try:
                parent_sec = self._get_section(db, domaindoc_id, parent)
            except ValueError:
                # Parent not found as top-level section - check if it's a subsection
                subsec_check = db.fetchone(
                    "SELECT parent_section_id FROM domaindoc_sections WHERE domaindoc_id = :doc_id AND header = :header",
                    {"doc_id": domaindoc_id, "header": self._normalize_section_name(parent)}
                )
                if subsec_check and subsec_check.get("parent_section_id") is not None:
                    raise ValueError(f"Maximum nesting depth is 1. '{parent}' is already a subsection.")
                raise  # Re-raise original "not found" error

            # Depth check: parent cannot itself be a subsection
            if parent_sec.get("parent_section_id") is not None:
                raise ValueError(f"Maximum nesting depth is 1. '{parent}' is already a subsection.")

            # OVERVIEW exception: first section cannot have subsections
            if parent_sec["sort_order"] == 0:
                raise ValueError("Cannot add subsections to the overview section")

            parent_section_id = parent_sec["id"]
            # Get siblings for ordering
            all_sections = self._get_all_sections(db, domaindoc_id, parent_id=parent_section_id)
        else:
            # Creating top-level section
            all_sections = self._get_all_sections(db, domaindoc_id)

        if after:
            after_sec = self._get_section(db, domaindoc_id, after, parent)
            new_order = after_sec["sort_order"] + 1
            for sec in all_sections:
                if sec["sort_order"] >= new_order:
                    db.execute(
                        "UPDATE domaindoc_sections SET sort_order = sort_order + 1 WHERE id = :id",
                        {"id": sec["id"]}
                    )
        else:
            new_order = max((s["sort_order"] for s in all_sections), default=-1) + 1

        # expanded_by_default sections start expanded; others start collapsed
        start_expanded = expanded_by_default is True
        section_id = db.insert("domaindoc_sections", {
            "domaindoc_id": domaindoc_id,
            "parent_section_id": parent_section_id,
            "header": header,
            "encrypted__content": content,
            "sort_order": new_order,
            "collapsed": not start_expanded,
            "expanded_by_default": start_expanded,
            "created_at": now,
            "updated_at": now
        })

        self._record_version(db, domaindoc_id, "create_section", {
            "header": header,
            "content_length": len(content),
            "after": after,
            "parent": parent,
            "expanded_by_default": start_expanded
        }, int(section_id))

        self._update_domaindoc_timestamp(db, domaindoc_id)
        result = {"success": True, "created": header, "sort_order": new_order, "parent": parent}
        if start_expanded:
            result["expanded_by_default"] = True
        return result

    def _op_rename_section(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        new_name: Optional[str],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Rename a section header."""
        if not section:
            raise ValueError("rename_section requires 'section' parameter")
        if not new_name:
            raise ValueError("rename_section requires 'new_name' parameter")

        sec = self._get_section(db, domaindoc_id, section, parent)
        old_name = sec["header"]
        normalized_new = self._normalize_section_name(new_name)
        now = format_utc_iso(utc_now())

        db.execute(
            "UPDATE domaindoc_sections SET header = :new_name, updated_at = :now WHERE id = :id",
            {"new_name": normalized_new, "now": now, "id": sec["id"]}
        )

        self._record_version(db, domaindoc_id, "rename_section", {
            "old_name": old_name,
            "new_name": normalized_new,
            "parent": parent
        }, sec["id"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {"success": True, "renamed": old_name, "to": normalized_new, "parent": parent}

    def _op_delete_section(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Delete a section. Must be expanded first. If parent, all subsections must be expanded."""
        if not section:
            raise ValueError("delete_section requires 'section' parameter")

        sec = self._get_section(db, domaindoc_id, section, parent)

        if sec["collapsed"]:
            raise ValueError(
                f"Please expand '{sec['header']}' before deleting to confirm you've reviewed its contents"
            )

        # First top-level section cannot be deleted
        if sec["sort_order"] == 0 and sec.get("parent_section_id") is None:
            raise ValueError("Cannot delete the first section (overview)")

        # If this is a parent with subsections, all subsections must be expanded
        subsections = self._get_subsections(db, sec["id"])
        if subsections:
            collapsed_subs = [s["header"] for s in subsections if s.get("collapsed")]
            if collapsed_subs:
                raise ValueError(
                    f"Please expand all subsections of '{sec['header']}' before deleting: {collapsed_subs}"
                )

        deleted_children = []
        if subsections:
            # Record and delete subsections first
            for sub in subsections:
                self._record_version(db, domaindoc_id, "delete_section", {
                    "header": sub["header"],
                    "deleted_content": sub.get("encrypted__content", ""),
                    "sort_order": sub["sort_order"],
                    "parent": sec["header"]
                }, sub["id"])
                deleted_children.append(sub["header"])
            # Cascade delete handled by FK ON DELETE CASCADE

        self._record_version(db, domaindoc_id, "delete_section", {
            "header": sec["header"],
            "deleted_content": sec.get("encrypted__content", ""),
            "sort_order": sec["sort_order"],
            "parent": parent,
            "deleted_children": deleted_children
        }, sec["id"])

        db.execute(
            "DELETE FROM domaindoc_sections WHERE id = :id",
            {"id": sec["id"]}
        )

        # Renumber siblings
        parent_id = sec.get("parent_section_id")
        siblings = self._get_all_sections(db, domaindoc_id, parent_id=parent_id) if parent_id else self._get_all_sections(db, domaindoc_id)
        for i, s in enumerate(siblings):
            if s["sort_order"] != i:
                db.execute(
                    "UPDATE domaindoc_sections SET sort_order = :order WHERE id = :id",
                    {"order": i, "id": s["id"]}
                )

        self._update_domaindoc_timestamp(db, domaindoc_id)
        result = {"success": True, "deleted": sec["header"], "parent": parent}
        if deleted_children:
            result["deleted_children"] = deleted_children
        return result

    def _op_reorder_sections(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        order: Optional[List[str]],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Reorder sections at a given level. Use parent to reorder subsections."""
        if not order:
            raise ValueError("reorder_sections requires 'order' parameter")

        if parent:
            parent_sec = self._get_section(db, domaindoc_id, parent)
            all_sections = self._get_all_sections(db, domaindoc_id, parent_id=parent_sec["id"])
        else:
            all_sections = self._get_all_sections(db, domaindoc_id)

        existing_headers = {s["header"] for s in all_sections}
        provided_headers = {self._normalize_section_name(h) for h in order}

        missing = existing_headers - provided_headers
        unknown = provided_headers - existing_headers

        if missing or unknown:
            parts = []
            if missing:
                parts.append(f"missing sections {list(missing)}")
            if unknown:
                parts.append(f"unknown sections {list(unknown)}")
            raise ValueError(f"Reorder failed: {' and '.join(parts)}")

        now = format_utc_iso(utc_now())
        for new_order, header in enumerate(order):
            normalized = self._normalize_section_name(header)
            sec = next(s for s in all_sections if s["header"] == normalized)
            db.execute(
                "UPDATE domaindoc_sections SET sort_order = :order, updated_at = :now WHERE id = :id",
                {"order": new_order, "now": now, "id": sec["id"]}
            )

        self._record_version(db, domaindoc_id, "reorder_sections", {"order": order, "parent": parent})
        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {"success": True, "new_order": order, "parent": parent}

    # =========================================================================
    # Content Editing Operations
    # =========================================================================

    def _op_append(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        content: Optional[str],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Append content to a section or subsection."""
        if not section:
            raise ValueError("append requires 'section' parameter")
        if not content:
            raise ValueError("append requires 'content' parameter")

        sec = self._get_section(db, domaindoc_id, section, parent)
        current = sec.get("encrypted__content", "")
        if current and not current.endswith('\n'):
            current += '\n'
        new_content = current + content
        now = format_utc_iso(utc_now())

        db.update(
            "domaindoc_sections",
            {"encrypted__content": new_content, "updated_at": now},
            "id = :id",
            {"id": sec["id"]}
        )

        self._record_version(db, domaindoc_id, "append", {
            "section": sec["header"],
            "appended_content": content,
            "result_length": len(new_content),
            "parent": parent
        }, sec["id"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {
            "success": True,
            "section": sec["header"],
            "appended_chars": len(content),
            "total_chars": len(new_content)
        }

    def _op_sed(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        find: Optional[str],
        replace: Optional[str],
        global_replace: bool,
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Replace text in a section or subsection."""
        if not section:
            raise ValueError("sed requires 'section' parameter")
        if not find:
            raise ValueError("sed requires 'find' parameter")
        if replace is None:
            raise ValueError("sed requires 'replace' parameter")

        sec = self._get_section(db, domaindoc_id, section, parent)
        current = sec.get("encrypted__content", "")

        if global_replace:
            new_content = current.replace(find, replace)
            count = current.count(find)
        else:
            new_content = current.replace(find, replace, 1)
            count = 1 if find in current else 0

        if count == 0:
            return {
                "success": False,
                "section": sec["header"],
                "message": f"Pattern '{find}' not found in section",
                "parent": parent
            }

        now = format_utc_iso(utc_now())
        db.update(
            "domaindoc_sections",
            {"encrypted__content": new_content, "updated_at": now},
            "id = :id",
            {"id": sec["id"]}
        )

        op_name = "sed_all" if global_replace else "sed"
        self._record_version(db, domaindoc_id, op_name, {
            "section": sec["header"],
            "find": find,
            "replace": replace,
            "replacements": count,
            "parent": parent
        }, sec["id"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {
            "success": True,
            "section": sec["header"],
            "replacements": count,
            "total_chars": len(new_content),
            "parent": parent
        }

    def _op_replace_section(
        self,
        db: UserDataManager,
        domaindoc_id: int,
        section: Optional[str],
        content: Optional[str],
        parent: Optional[str] = None
    ) -> Dict[str, Any]:
        """Replace entire section or subsection content."""
        if not section:
            raise ValueError("replace_section requires 'section' parameter")
        if content is None:
            raise ValueError("replace_section requires 'content' parameter")

        sec = self._get_section(db, domaindoc_id, section, parent)
        previous_content = sec.get("encrypted__content", "")  # Capture BEFORE modification
        now = format_utc_iso(utc_now())

        db.update(
            "domaindoc_sections",
            {"encrypted__content": content, "updated_at": now},
            "id = :id",
            {"id": sec["id"]}
        )

        self._record_version(db, domaindoc_id, "replace_section", {
            "section": sec["header"],
            "old_length": len(previous_content),
            "new_length": len(content),
            "previous_content": previous_content,
            "parent": parent
        }, sec["id"])

        self._update_domaindoc_timestamp(db, domaindoc_id)
        return {
            "success": True,
            "section": sec["header"],
            "previous_chars": len(previous_content),
            "new_chars": len(content),
            "parent": parent
        }

    # =========================================================================
    # Helpers
    # =========================================================================

    def _resolve_section_targets(
        self,
        section: Optional[str],
        sections: Optional[List[str]]
    ) -> List[str]:
        """Resolve section or sections parameter to list of headers."""
        if sections:
            return [self._normalize_section_name(s) for s in sections]
        elif section:
            return [self._normalize_section_name(section)]
        return []

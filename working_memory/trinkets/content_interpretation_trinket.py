"""
Content interpretation protocol trinket.

Injects a reasoning protocol into the system prompt when tool results
require careful interpretation - forcing explicit source/content verification
before the LLM describes content.

This addresses the "Source ≠ Substance" problem: LLMs pattern-match on sender/source
metadata and assume content type, then describe through that lens without verification.
Example: Vendor email assumed to be transaction when it's actually a newsletter.
"""
import json
import logging
from typing import Dict, Any, List, Set

from .base import EventAwareTrinket

logger = logging.getLogger(__name__)


# Tools whose results may require content interpretation
CONTENT_INTERPRETATION_TOOLS: Set[str] = {
    "email_tool",
    "web_tool",
    "document_tool",
    "file_tool",
}


class ContentInterpretationTrinket(EventAwareTrinket):
    """
    Injects reasoning protocol when tool results require careful interpretation.

    Activates when tools return content where source metadata could mislead
    about content substance (emails from brands, web content from domains, etc.)

    The protocol forces explicit verification before description:
    1. Observe source/sender/metadata
    2. State initial assumption
    3. Verify actual content matches assumption
    4. Classify based on content substance, not source identity
    5. Flag mismatches explicitly
    """

    cache_policy = False  # Dynamic, changes based on recent tool calls

    def __init__(self, event_bus, working_memory):
        super().__init__(event_bus, working_memory)

        # Activation state
        self.active = False
        self.active_context: Dict[str, Any] = {}
        self.activation_turn = 0
        self.current_turn = 0
        self.ttl_turns = 2  # Deactivate after N turns without relevant tool calls

        # Subscribe to turn completion to check for content-interpretation tools
        self.event_bus.subscribe('TurnCompletedEvent', self._handle_turn_completed)
        logger.info("ContentInterpretationTrinket subscribed to TurnCompletedEvent")

    def _get_variable_name(self) -> str:
        return "content_interpretation"

    def _handle_turn_completed(self, event) -> None:
        """
        Check if turn included content-interpretation tools.

        Activates protocol if relevant tools were used, deactivates after TTL.
        """
        self.current_turn = event.turn_number
        continuum = event.continuum

        # Check recent messages for content-interpretation tool results
        tools_used = self._extract_content_tools_from_turn(continuum)

        if tools_used:
            self.active = True
            self.activation_turn = self.current_turn
            self.active_context = self._build_context_hints(tools_used)
            logger.info(
                f"ContentInterpretationTrinket activated at turn {self.current_turn} "
                f"for tools: {tools_used}"
            )
        elif self.active and (self.current_turn - self.activation_turn) >= self.ttl_turns:
            self.active = False
            self.active_context = {}
            logger.debug(
                f"ContentInterpretationTrinket deactivated after {self.ttl_turns} turns"
            )

    def _extract_content_tools_from_turn(self, continuum) -> List[str]:
        """
        Extract content-interpretation tools used in the most recent turn.

        Scans recent messages for tool results from content-interpretation tools.
        """
        tools_found = []

        # Check recent messages (last 10 should be enough for a single turn)
        recent_messages = continuum.messages[-10:] if continuum.messages else []

        for msg in recent_messages:
            # Check assistant messages for tool calls
            if msg.role == "assistant" and msg.metadata.get("has_tool_calls"):
                tool_calls = msg.metadata.get("tool_calls", [])
                for tc in tool_calls:
                    tool_name = tc.get("name", "")
                    if tool_name in CONTENT_INTERPRETATION_TOOLS:
                        tools_found.append(tool_name)

        return list(set(tools_found))  # Deduplicate

    def _build_context_hints(self, tools_used: List[str]) -> Dict[str, Any]:
        """Build context hints based on which tools were used."""
        hints: Dict[str, Any] = {"tools": tools_used}

        # Add tool-specific hints
        if "email_tool" in tools_used:
            hints["content_types"] = hints.get("content_types", []) + ["email"]
            hints["key_distinctions"] = hints.get("key_distinctions", []) + [
                "transactional vs. promotional/newsletter"
            ]

        if "web_tool" in tools_used:
            hints["content_types"] = hints.get("content_types", []) + ["web content"]
            hints["key_distinctions"] = hints.get("key_distinctions", []) + [
                "primary content vs. ads/navigation"
            ]

        if "document_tool" in tools_used or "file_tool" in tools_used:
            hints["content_types"] = hints.get("content_types", []) + ["document"]
            hints["key_distinctions"] = hints.get("key_distinctions", []) + [
                "document type based on content, not filename"
            ]

        return hints

    def generate_content(self, context: Dict[str, Any]) -> str:
        """Generate interpretation protocol if active."""
        if not self.active:
            return ""

        content_types = ", ".join(self.active_context.get("content_types", ["content"]))
        key_distinctions = "; ".join(
            self.active_context.get("key_distinctions", ["content type"])
        )

        return f"""<content_interpretation_protocol>
<context>You recently retrieved {content_types} that requires careful interpretation.
Source metadata (sender, domain, filename) may not indicate actual content type.</context>

<verify_before_describe>
Before describing any item's content:
1. OBSERVE: Note the source/sender/metadata
2. ASSUME: State what type you'd initially expect from this source
3. VERIFY: Check if actual content matches that expectation
4. CLASSIFY: Determine what the content actually IS based on substance
5. FLAG: If assumption ≠ reality, note the mismatch explicitly
</verify_before_describe>

<key_distinctions>{key_distinctions}</key_distinctions>

<common_mismatches>
- Familiar brand sender + promotional content (not a transaction)
- Official-looking source + marketing material (not account action required)
- Personal name + forwarded/automated content (not direct message)
- Professional filename + informal/draft content (not final document)
</common_mismatches>

<output_format>
For each item, output classification before description:
[VERIFIED: {{type}}] Description based on verified content...
[MISMATCH: expected {{X}}, actually {{Y}}] Description noting the discrepancy...
</output_format>
</content_interpretation_protocol>"""


"""Example: a security specialist. REPLACE with your own domain specialist."""

from ..base_agent import BaseAgent


class SecuritySpecialist(BaseAgent):
    """Answers security / identity / secret-handling questions, grounded in the security KB."""

    knowledge_dirs = ["tier_a/security", "tier_b/security"]

    def __init__(self):
        super().__init__("security")

    def get_system_prompt(self) -> str:
        return (
            "You are the Security specialist for a governed builder companion. Answer security, "
            "identity, auth, and data-handling questions grounded ONLY in the knowledge base. "
            "Never emit a credential, key, or secret in your output (Tenet 6). Systems of record "
            "are read-only (Tenet 3). Outbound access is allowlist-only and you never "
            "fetch-and-execute remote content (Tenet 11). If the knowledge base lacks the answer, "
            "say so — do not guess."
        )

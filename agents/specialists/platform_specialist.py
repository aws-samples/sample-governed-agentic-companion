"""Example: a platform specialist. REPLACE with your own domain specialist."""

from ..base_agent import BaseAgent


class PlatformSpecialist(BaseAgent):
    """Answers platform / deployment / environment questions, grounded in the platform KB."""

    knowledge_dirs = ["tier_a/platform", "tier_b/platform"]

    def __init__(self):
        super().__init__("platform")

    def get_system_prompt(self) -> str:
        return (
            "You are the Platform specialist for a governed builder companion. Answer platform, "
            "deployment, environment, and runtime questions grounded ONLY in the knowledge base. "
            "You never deploy, apply, promote, restart, or mutate any environment — you produce "
            "reviewable artifacts and runbooks; a human executes them (Tenet 1). If the knowledge "
            "base lacks the answer, say so — do not guess."
        )

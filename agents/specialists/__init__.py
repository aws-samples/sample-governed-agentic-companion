"""Example specialists. REPLACE these with your own domain specialists.

Each specialist is a small class over BaseAgent that declares the knowledge subdirectories it
grounds against. Routing keywords are declared alongside so the Orchestrator can match intent.
Add/replace entries in build_specialists() — routing and gating come for free.
"""

from typing import Dict, List, Tuple

from .platform_specialist import PlatformSpecialist
from .security_specialist import SecuritySpecialist


def build_specialists() -> Dict[str, Tuple[object, List[str]]]:
    """name -> (agent instance, routing keywords). Edit this to add your specialists."""
    return {
        "platform": (PlatformSpecialist(),
                     ["deploy", "deployment", "environment", "infrastructure", "promote",
                      "release", "runtime", "scale", "region"]),
        "security": (SecuritySpecialist(),
                     ["security", "secret", "credential", "auth", "token", "tls", "egress",
                      "permission", "access", "encrypt"]),
    }

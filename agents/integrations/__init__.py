"""Integration clients — the pattern for talking to external systems (SoRs, collaboration
surfaces) from a governed companion.

REPLACE / EXTEND per engagement. This package ships ONE example — a generic REST client
(`rest_client.RestClient`) — that demonstrates the security posture every integration in this
kit must follow:

  - TLS certificate verification is ALWAYS on. There is no supported way to disable it; behind
    a TLS-intercepting corporate proxy, TRUST the CA via REQUESTS_CA_BUNDLE (never disable
    verification). See RestClient._resolve_verify.
  - Secrets come from the environment (or a fork's secret manager), never hard-coded.
  - Read/write boundary is the caller's responsibility to honour (Tenet 3): a client MAY be
    able to POST, but the governed agents only WRITE to human-reviewed surfaces (issue
    trackers, wikis, VCS), never to a system of record.

A fork adds `jira_client.py`, `github_client.py`, etc. by subclassing RestClient or copying
its shape. The kit intentionally ships no product-specific client.
"""

from .rest_client import RestClient

__all__ = ["RestClient"]

"""The diagram specialist's engine: a pure-Python, LLM-free, network-free pipeline.

facts/topics (KB data) -> labels (policy) -> compose (traceable scene) -> layout + route
(deterministic geometry) -> quality (measured review rules) -> emit_svg / emit_drawio / raster
-> report / bundle / manifest (bounded, gated text). See docs/ARCHITECTURE.md.

Describe-only (Tenets 1 and 3): nothing here deploys, mutates, spawns a process or opens a
socket. Every string that reaches an artefact is a policy-checked scene label or a fixed template.
"""

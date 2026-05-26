"""PaperHub config-driven real-API benchmark harness.

Drives the user's live backend (:8000) as a simulated user, attaching cached
reference papers and routing prompts through /chat, then gathers grounding
evidence (cited chunk text + trace) for 0/1 review. See README.md.
"""

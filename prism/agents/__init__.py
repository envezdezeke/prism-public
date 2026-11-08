"""Nemotron agents (Phase 6). Off the scored path.

Module map:
- client: the shared Nemotron client (modes, .env, urllib transport, digest, record/replay cache, retries,
  ChatClient for OpenAI-format tool-calling chat). 06-01.
- tools, investigator, routes: the read-only fraud investigator and its API routes. 06-02.
- redteam: red-team lite. 06-03.

Rule: nothing in prism/agents may set a scored field. The only scored-path importer is prism/read/vl.py,
and it imports only client.
"""

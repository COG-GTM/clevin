"""Probe 0: inspect existing resources and the multiagent surface.

Read-only. Prints the production agent's config (including its multiagent roster
state), its published versions, and the available environments. Creates nothing.
"""

from __future__ import annotations

import json
import os

import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

agent_id = os.environ["CLEVIN_AGENT_ID"]
agent = client.beta.agents.retrieve(agent_id)
print("AGENT", json.dumps(agent.model_dump(mode="json"), indent=2)[:1500])

print("\nENVIRONMENTS")
for env in client.beta.environments.list():
    print(json.dumps(env.model_dump(mode="json"), indent=2))

print("\nVERSIONS")
for v in client.beta.agents.versions.list(agent_id):
    d = v.model_dump(mode="json")
    print(v.version, d.get("model"), "skills", d.get("skills"), "multiagent", d.get("multiagent"))

import { describe, expect, it } from "vitest";
import { agentDefinition } from "../src/agent-definition.js";
import { desiredAgentState, diffAgentState } from "../src/drift.js";

describe("diffAgentState", () => {
  it("reports no changes for identical states", () => {
    const state = { system: "prompt", model: { id: "model" } };

    expect(diffAgentState(state, structuredClone(state))).toEqual({
      drift: [],
      serverAdded: [],
    });
  });

  it("reports a changed system value", () => {
    expect(diffAgentState({ system: "desired" }, { system: "actual" })).toEqual(
      {
        drift: [{ path: "system", desired: "desired", actual: "actual" }],
        serverAdded: [],
      },
    );
  });

  it("normalizes effort strings and objects", () => {
    expect(
      diffAgentState(
        { model: { id: "model", effort: "high" } },
        { model: { id: "model", effort: { type: "high" } } },
      ),
    ).toEqual({ drift: [], serverAdded: [] });
  });

  it("normalizes a bare model string and model object", () => {
    expect(
      diffAgentState({ model: "model" }, { model: { id: "model" } }),
    ).toEqual({ drift: [], serverAdded: [] });
  });

  it("reports server-added model fields without drift", () => {
    expect(
      diffAgentState(
        { model: { id: "model", effort: "medium" } },
        {
          model: {
            id: "model",
            effort: { type: "medium" },
            inference_geo: null,
            speed: "standard",
          },
        },
      ),
    ).toEqual({
      drift: [],
      serverAdded: ["model.inference_geo", "model.speed"],
    });
  });

  it("reports an extra metadata key as server-added", () => {
    expect(
      diffAgentState(
        { metadata: { source_of_truth: "typescript" } },
        {
          metadata: {
            source_of_truth: "typescript",
            server_value: "managed",
          },
        },
      ),
    ).toEqual({
      drift: [],
      serverAdded: ["metadata.server_value"],
    });
  });

  it("reports a missing metadata key as drift", () => {
    expect(
      diffAgentState(
        { metadata: { source_of_truth: "typescript" } },
        { metadata: {} },
      ),
    ).toEqual({
      drift: [
        {
          path: "metadata.source_of_truth",
          desired: "typescript",
          actual: undefined,
        },
      ],
      serverAdded: [],
    });
  });

  it("reports nested tool configuration changes with an indexed path", () => {
    expect(
      diffAgentState(
        { tools: [{ default_config: { enabled: true } }] },
        { tools: [{ default_config: { enabled: false } }] },
      ),
    ).toEqual({
      drift: [
        {
          path: "tools[0].default_config.enabled",
          desired: true,
          actual: false,
        },
      ],
      serverAdded: [],
    });
  });

  it("reports an array length mismatch at the array path", () => {
    expect(diffAgentState({ tools: [{ type: "one" }] }, { tools: [] })).toEqual(
      {
        drift: [{ path: "tools", desired: [{ type: "one" }], actual: [] }],
        serverAdded: [],
      },
    );
  });
});

describe("desiredAgentState", () => {
  it("returns the managed fields as a deep copy", () => {
    const desired = desiredAgentState();

    expect(Object.keys(desired)).toEqual([
      "name",
      "description",
      "model",
      "system",
      "metadata",
      "mcp_servers",
      "tools",
      "skills",
      "multiagent",
    ]);

    const metadata = desired.metadata as Record<string, unknown>;
    metadata.source_of_truth = "mutated";
    const tools = desired.tools as Array<Record<string, unknown>>;
    tools.push({ type: "mutated" });
    const model = desired.model as Record<string, unknown>;
    model.id = "mutated";

    expect(agentDefinition.metadata.source_of_truth).toBe("typescript");
    expect(agentDefinition.tools).toHaveLength(3);
    expect(agentDefinition.model.id).toBe("claude-opus-5");
  });
});

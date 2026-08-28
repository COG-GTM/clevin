import type {
  AgentCreateParams,
  AgentUpdateParams,
} from "@anthropic-ai/sdk/resources/beta/agents/agents";
import type {
  EnvironmentCreateParams,
  EnvironmentUpdateParams,
} from "@anthropic-ai/sdk/resources/beta/environments/environments";
import type {
  MemoryStoreCreateParams,
  MemoryStoreUpdateParams,
} from "@anthropic-ai/sdk/resources/beta/memory-stores/memory-stores";
import type {
  CredentialCreateParams,
  CredentialUpdateParams,
} from "@anthropic-ai/sdk/resources/beta/vaults/credentials";
import type {
  VaultCreateParams,
  VaultUpdateParams,
} from "@anthropic-ai/sdk/resources/beta/vaults/vaults";
import { describe, expect, it, vi } from "vitest";
import {
  agentDefinition,
  adversarialReviewerDefinition,
  coordinatorRoster,
  CLEVIN_SYSTEM_PROMPT,
  GITHUB_MCP_URL,
  LINEAR_MCP_URL,
  repositoryExplorerDefinition,
  SUBAGENT_DEFINITIONS,
  testDebuggerDefinition,
} from "../src/agent-definition.js";
import {
  ConfigurationError,
  type ProvisionConfig,
  parseProvisionConfig,
} from "../src/config.js";
import { provision, serializeManifest } from "../src/provision.js";
import {
  type ProvisioningClient,
  ResourceConfigurationError,
} from "../src/resources.js";

const secrets = {
  anthropicApiKey: "anthropic-secret-for-test",
  githubToken: "github-secret-for-test",
  linearApiKey: "linear-secret-for-test",
} satisfies ProvisionConfig;

function mockClient(agentVersion = 4) {
  const createdSubagentIds = [
    "agent_explorer_created",
    "agent_debugger_created",
    "agent_reviewer_created",
  ];
  let subagentCreateIndex = 0;
  const client = {
    beta: {
      agents: {
        create: vi.fn(async (params: AgentCreateParams) => {
          if (params.name === agentDefinition.name) {
            return { id: "agent_created", version: 1 };
          }
          return {
            id: createdSubagentIds[subagentCreateIndex++] ?? "agent_extra",
            version: 1,
          };
        }),
        retrieve: vi.fn(async (_id: string) => ({
          id: "agent_existing",
          version: agentVersion,
        })),
        update: vi.fn(async (id: string, _params: AgentUpdateParams) => ({
          id,
          version: agentVersion + 1,
        })),
      },
      environments: {
        create: vi.fn(async (_params: EnvironmentCreateParams) => ({
          id: "env_created",
          config: { type: "self_hosted" as const },
        })),
        retrieve: vi.fn(async (id: string) => ({
          id,
          config: { type: "self_hosted" as const },
        })),
        update: vi.fn(async (id: string, _params: EnvironmentUpdateParams) => ({
          id,
          config: { type: "self_hosted" as const },
        })),
      },
      memoryStores: {
        create: vi.fn(async (_params: MemoryStoreCreateParams) => ({
          id: "memstore_created",
        })),
        retrieve: vi.fn(async (id: string) => ({ id })),
        update: vi.fn(async (id: string, _params: MemoryStoreUpdateParams) => ({
          id,
        })),
      },
      vaults: {
        create: vi.fn(async (_params: VaultCreateParams) => ({
          id: "vlt_created",
        })),
        retrieve: vi.fn(async (id: string) => ({ id })),
        update: vi.fn(async (id: string, _params: VaultUpdateParams) => ({
          id,
        })),
        credentials: {
          create: vi.fn(
            async (_vaultId: string, params: CredentialCreateParams) => {
              if (params.auth.type !== "static_bearer") {
                throw new Error("Test expected static bearer authentication");
              }
              return {
                id:
                  params.auth.mcp_server_url === LINEAR_MCP_URL
                    ? "vcrd_linear_created"
                    : "vcrd_github_created",
                auth: {
                  type: "static_bearer" as const,
                  mcp_server_url: params.auth.mcp_server_url,
                },
              };
            },
          ),
          retrieve: vi.fn(
            async (id: string, _params: { vault_id: string }) => ({
              id,
              auth: {
                type: "static_bearer" as const,
                mcp_server_url: id.includes("linear")
                  ? LINEAR_MCP_URL
                  : GITHUB_MCP_URL.slice(0, -1),
              },
            }),
          ),
          update: vi.fn(
            async (id: string, _params: CredentialUpdateParams) => ({
              id,
              auth: {
                type: "static_bearer" as const,
                mcp_server_url: id.includes("linear")
                  ? LINEAR_MCP_URL
                  : GITHUB_MCP_URL.slice(0, -1),
              },
            }),
          ),
        },
      },
    },
  } satisfies ProvisioningClient;
  return client;
}

describe("agent definition", () => {
  it("uses the exact model, MCP URLs, full native toolset, and autonomous permissions", () => {
    expect(agentDefinition.model).toEqual({
      id: "claude-opus-5",
      effort: "medium",
    });
    expect(agentDefinition.mcp_servers).toEqual([
      { type: "url", name: "linear", url: "https://mcp.linear.app/mcp" },
      {
        type: "url",
        name: "github",
        url: "https://api.githubcopilot.com/mcp/",
      },
    ]);
    expect(agentDefinition.tools).toHaveLength(3);
    expect(agentDefinition.tools[0]).toMatchObject({
      type: "agent_toolset_20260401",
      default_config: {
        enabled: true,
        permission_policy: { type: "always_allow" },
      },
    });
    for (const toolset of agentDefinition.tools) {
      expect(toolset.default_config).toEqual({
        enabled: true,
        permission_policy: { type: "always_allow" },
      });
      expect(toolset).not.toHaveProperty("configs");
    }
    expect(agentDefinition.skills).toEqual([]);
    expect(agentDefinition.multiagent).toBeNull();
    expect(agentDefinition).not.toHaveProperty("outcomes");
    expect(agentDefinition.tools.map((tool) => tool.type)).toEqual([
      "agent_toolset_20260401",
      "mcp_toolset",
      "mcp_toolset",
    ]);
    expect(CLEVIN_SYSTEM_PROMPT).toContain("exactly one relevant repository");
    expect(CLEVIN_SYSTEM_PROMPT).toContain("CLEVIN_SMOKE_TEST");
    expect(CLEVIN_SYSTEM_PROMPT).toContain("timeout_ms");
    expect(CLEVIN_SYSTEM_PROMPT).toContain("restart=true");
    expect(CLEVIN_SYSTEM_PROMPT).toContain(
      "Do not print or expose credentials",
    );
    expect(CLEVIN_SYSTEM_PROMPT).toContain(
      "Do not claim success until required CI checks are green",
    );
    expect(SUBAGENT_DEFINITIONS).toEqual([
      repositoryExplorerDefinition,
      testDebuggerDefinition,
      adversarialReviewerDefinition,
    ]);
    expect(repositoryExplorerDefinition.tools[0]).toMatchObject({
      configs: [
        { type: "write", name: "write", enabled: false },
        { type: "edit", name: "edit", enabled: false },
      ],
    });
    expect(testDebuggerDefinition.tools[0]).not.toHaveProperty("configs");
    expect(adversarialReviewerDefinition.tools[0]).toMatchObject({
      configs: [
        { type: "write", name: "write", enabled: false },
        { type: "edit", name: "edit", enabled: false },
      ],
    });
  });
});

describe("configuration", () => {
  it("parses secrets and typed optional resource IDs", () => {
    expect(
      parseProvisionConfig({
        ANTHROPIC_API_KEY: secrets.anthropicApiKey,
        GITHUB_TOKEN: secrets.githubToken,
        LINEAR_API_KEY: secrets.linearApiKey,
        CLEVIN_AGENT_ID: "agent_existing",
        CLEVIN_AGENT_VERSION: "4",
        CLEVIN_SUBAGENT_IDS:
          " agent_explorer_existing,agent_debugger_existing, agent_reviewer_existing ",
        CLEVIN_ENVIRONMENT_ID: "env_existing",
        CLEVIN_VAULT_ID: "vlt_existing",
        CLEVIN_LINEAR_CREDENTIAL_ID: "vcrd_linear_existing",
        CLEVIN_GITHUB_CREDENTIAL_ID: "vcrd_github_existing",
        CLEVIN_MEMORY_STORE_ID: "memstore_existing",
      }),
    ).toEqual({
      ...secrets,
      agentId: "agent_existing",
      agentVersion: 4,
      subagentIds: [
        "agent_explorer_existing",
        "agent_debugger_existing",
        "agent_reviewer_existing",
      ],
      environmentId: "env_existing",
      vaultId: "vlt_existing",
      linearCredentialId: "vcrd_linear_existing",
      githubCredentialId: "vcrd_github_existing",
      memoryStoreId: "memstore_existing",
    });
  });

  it("parses an empty subagent list as undefined", () => {
    expect(
      parseProvisionConfig({
        ANTHROPIC_API_KEY: secrets.anthropicApiKey,
        GITHUB_TOKEN: secrets.githubToken,
        LINEAR_API_KEY: secrets.linearApiKey,
        CLEVIN_SUBAGENT_IDS: "  ",
      }),
    ).toEqual(secrets);
  });

  it("rejects an invalid subagent list", () => {
    expect(() =>
      parseProvisionConfig({
        ...secrets,
        CLEVIN_SUBAGENT_IDS: "agent_one,agent_two",
      }),
    ).toThrow(
      new ConfigurationError(
        `CLEVIN_SUBAGENT_IDS must contain exactly ${SUBAGENT_DEFINITIONS.length} IDs`,
      ),
    );
    expect(() =>
      parseProvisionConfig({
        ...secrets,
        CLEVIN_SUBAGENT_IDS: "agent_one,not_an_agent,agent_three",
      }),
    ).toThrow(
      new ConfigurationError(
        "CLEVIN_SUBAGENT_IDS must be a valid agent_ resource ID",
      ),
    );
  });

  it("rejects invalid coordinator roster arguments", () => {
    expect(() => coordinatorRoster(["agent_one"])).toThrow(
      `Expected ${SUBAGENT_DEFINITIONS.length} agent_ subagent IDs`,
    );
    expect(() =>
      coordinatorRoster(["agent_one", "not_an_agent", "agent_three"]),
    ).toThrow(`Expected ${SUBAGENT_DEFINITIONS.length} agent_ subagent IDs`);
  });

  it("fails fast without exposing secret values", () => {
    expect(() =>
      parseProvisionConfig({
        ANTHROPIC_API_KEY: secrets.anthropicApiKey,
        GITHUB_TOKEN: secrets.githubToken,
        LINEAR_API_KEY: "   ",
      }),
    ).toThrow(new ConfigurationError("LINEAR_API_KEY is required"));
    expect(() =>
      parseProvisionConfig({
        ANTHROPIC_API_KEY: secrets.anthropicApiKey,
        GITHUB_TOKEN: secrets.githubToken,
        LINEAR_API_KEY: secrets.linearApiKey,
        CLEVIN_AGENT_ID: "agent_existing",
      }),
    ).toThrow(
      "CLEVIN_AGENT_ID and CLEVIN_AGENT_VERSION must be configured together",
    );
  });
});

describe("resource reconciliation", () => {
  it("creates every resource and exact static bearer credential when IDs are absent", async () => {
    const client = mockClient();
    const manifest = await provision(client, secrets);

    expect(client.beta.environments.create).toHaveBeenCalledWith(
      expect.objectContaining({ config: { type: "self_hosted" } }),
    );
    expect(client.beta.memoryStores.create).toHaveBeenCalledOnce();
    expect(client.beta.vaults.create).toHaveBeenCalledOnce();
    expect(client.beta.vaults.credentials.create).toHaveBeenNthCalledWith(
      1,
      "vlt_created",
      expect.objectContaining({
        auth: {
          type: "static_bearer",
          mcp_server_url: LINEAR_MCP_URL,
          token: secrets.linearApiKey,
        },
      }),
    );
    expect(client.beta.vaults.credentials.create).toHaveBeenNthCalledWith(
      2,
      "vlt_created",
      expect.objectContaining({
        auth: {
          type: "static_bearer",
          mcp_server_url: GITHUB_MCP_URL,
          token: secrets.githubToken,
        },
      }),
    );
    expect(client.beta.agents.create).toHaveBeenCalledTimes(4);
    expect(client.beta.agents.create).toHaveBeenNthCalledWith(
      1,
      repositoryExplorerDefinition,
    );
    expect(client.beta.agents.create).toHaveBeenNthCalledWith(
      2,
      testDebuggerDefinition,
    );
    expect(client.beta.agents.create).toHaveBeenNthCalledWith(
      3,
      adversarialReviewerDefinition,
    );
    expect(client.beta.agents.create).toHaveBeenNthCalledWith(
      4,
      expect.objectContaining({
        ...agentDefinition,
        multiagent: coordinatorRoster([
          "agent_explorer_created",
          "agent_debugger_created",
          "agent_reviewer_created",
        ]),
      }),
    );
    expect(client.beta.agents.update).not.toHaveBeenCalled();
    expect(manifest).toMatchObject({
      agent_id: "agent_created",
      agent_version: 1,
      subagent_ids: [
        "agent_explorer_created",
        "agent_debugger_created",
        "agent_reviewer_created",
      ],
      environment_id: "env_created",
      memory_store_id: "memstore_created",
      vault_id: "vlt_created",
      linear_credential_id: "vcrd_linear_created",
      github_credential_id: "vcrd_github_created",
    });
  });

  it("retrieves and updates configured resources using the retrieved optimistic Agent version", async () => {
    const client = mockClient(4);
    const config: ProvisionConfig = {
      ...secrets,
      agentId: "agent_existing",
      agentVersion: 4,
      environmentId: "env_existing",
      memoryStoreId: "memstore_existing",
      vaultId: "vlt_existing",
      linearCredentialId: "vcrd_linear_existing",
      githubCredentialId: "vcrd_github_existing",
      subagentIds: [
        "agent_explorer_existing",
        "agent_debugger_existing",
        "agent_reviewer_existing",
      ],
    };

    const manifest = await provision(client, config);

    expect(client.beta.environments.retrieve).toHaveBeenCalledWith(
      "env_existing",
    );
    expect(client.beta.environments.update).toHaveBeenCalledWith(
      "env_existing",
      expect.objectContaining({ config: { type: "self_hosted" } }),
    );
    expect(client.beta.memoryStores.retrieve).toHaveBeenCalledWith(
      "memstore_existing",
    );
    expect(client.beta.memoryStores.update).toHaveBeenCalledWith(
      "memstore_existing",
      expect.objectContaining({ name: "Clevin Repository Learnings" }),
    );
    expect(client.beta.vaults.retrieve).toHaveBeenCalledWith("vlt_existing");
    expect(client.beta.vaults.update).toHaveBeenCalledWith(
      "vlt_existing",
      expect.objectContaining({ display_name: "Clevin MCP Credentials" }),
    );
    expect(client.beta.vaults.credentials.retrieve).toHaveBeenNthCalledWith(
      1,
      "vcrd_linear_existing",
      { vault_id: "vlt_existing" },
    );
    expect(client.beta.vaults.credentials.update).toHaveBeenNthCalledWith(
      1,
      "vcrd_linear_existing",
      expect.objectContaining({
        vault_id: "vlt_existing",
        auth: { type: "static_bearer", token: secrets.linearApiKey },
      }),
    );
    expect(client.beta.agents.retrieve).toHaveBeenCalledWith("agent_existing");
    expect(client.beta.agents.retrieve).toHaveBeenCalledWith(
      "agent_explorer_existing",
    );
    expect(client.beta.agents.retrieve).toHaveBeenCalledWith(
      "agent_debugger_existing",
    );
    expect(client.beta.agents.retrieve).toHaveBeenCalledWith(
      "agent_reviewer_existing",
    );
    expect(client.beta.agents.update).toHaveBeenCalledWith(
      "agent_existing",
      expect.objectContaining({
        version: 4,
        model: { id: "claude-opus-5", effort: "medium" },
        skills: [],
        multiagent: coordinatorRoster([
          "agent_explorer_existing",
          "agent_debugger_existing",
          "agent_reviewer_existing",
        ]),
      }),
    );
    expect(client.beta.agents.update).toHaveBeenCalledTimes(4);
    expect(manifest.agent_version).toBe(5);
    expect(manifest.subagent_ids).toEqual([
      "agent_explorer_existing",
      "agent_debugger_existing",
      "agent_reviewer_existing",
    ]);
    expect(client.beta.agents.create).not.toHaveBeenCalled();
    expect(client.beta.environments.create).not.toHaveBeenCalled();
    expect(client.beta.memoryStores.create).not.toHaveBeenCalled();
    expect(client.beta.vaults.create).not.toHaveBeenCalled();
    expect(client.beta.vaults.credentials.create).not.toHaveBeenCalled();
  });

  it("rejects a stale configured Agent version", async () => {
    const client = mockClient(5);
    await expect(
      provision(client, {
        ...secrets,
        agentId: "agent_existing",
        agentVersion: 4,
      }),
    ).rejects.toThrow(ResourceConfigurationError);
    expect(client.beta.agents.update).not.toHaveBeenCalled();
    expect(client.beta.environments.create).not.toHaveBeenCalled();
    expect(client.beta.memoryStores.create).not.toHaveBeenCalled();
    expect(client.beta.vaults.create).not.toHaveBeenCalled();
  });

  it("rejects a configured credential whose immutable MCP URL is not exact", async () => {
    const client = mockClient();
    client.beta.vaults.credentials.retrieve.mockResolvedValueOnce({
      id: "vcrd_linear_existing",
      auth: { type: "static_bearer", mcp_server_url: `${LINEAR_MCP_URL}/` },
    });

    await expect(
      provision(client, {
        ...secrets,
        vaultId: "vlt_existing",
        linearCredentialId: "vcrd_linear_existing",
      }),
    ).rejects.toThrow(
      `vcrd_linear_existing is not a static bearer credential for ${LINEAR_MCP_URL}`,
    );
    expect(client.beta.vaults.credentials.update).not.toHaveBeenCalled();
  });

  it("serializes only a machine-readable non-secret manifest", async () => {
    const manifest = await provision(mockClient(), secrets);
    const output = serializeManifest(manifest);
    expect(() => JSON.parse(output)).not.toThrow();
    expect(output).not.toContain(secrets.anthropicApiKey);
    expect(output).not.toContain(secrets.githubToken);
    expect(output).not.toContain(secrets.linearApiKey);
    expect(
      Object.keys(JSON.parse(output) as Record<string, unknown>).sort(),
    ).toEqual(
      [
        "agent_id",
        "agent_version",
        "environment_id",
        "github_credential_id",
        "linear_credential_id",
        "manual_next_step",
        "memory_store_id",
        "schema_version",
        "subagent_ids",
        "vault_id",
      ].sort(),
    );
  });
});

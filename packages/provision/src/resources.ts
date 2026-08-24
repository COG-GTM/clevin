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
import {
  agentDefinition,
  GITHUB_MCP_URL,
  LINEAR_MCP_URL,
} from "./agent-definition.js";
import type { ProvisionConfig } from "./config.js";

type Awaitable<T> = PromiseLike<T>;

interface AgentResult {
  id: string;
  version: number;
}

interface EnvironmentResult {
  id: string;
  config: { type: "cloud" | "self_hosted" };
}

interface IdentifiedResult {
  id: string;
}

interface CredentialResult {
  id: string;
  auth: {
    type: "mcp_oauth" | "static_bearer" | "environment_variable";
    mcp_server_url?: string;
  };
}

export interface ProvisioningClient {
  beta: {
    agents: {
      create(params: AgentCreateParams): Awaitable<AgentResult>;
      retrieve(agentId: string): Awaitable<AgentResult>;
      update(
        agentId: string,
        params: AgentUpdateParams,
      ): Awaitable<AgentResult>;
    };
    environments: {
      create(params: EnvironmentCreateParams): Awaitable<EnvironmentResult>;
      retrieve(environmentId: string): Awaitable<EnvironmentResult>;
      update(
        environmentId: string,
        params: EnvironmentUpdateParams,
      ): Awaitable<EnvironmentResult>;
    };
    memoryStores: {
      create(params: MemoryStoreCreateParams): Awaitable<IdentifiedResult>;
      retrieve(memoryStoreId: string): Awaitable<IdentifiedResult>;
      update(
        memoryStoreId: string,
        params: MemoryStoreUpdateParams,
      ): Awaitable<IdentifiedResult>;
    };
    vaults: {
      create(params: VaultCreateParams): Awaitable<IdentifiedResult>;
      retrieve(vaultId: string): Awaitable<IdentifiedResult>;
      update(
        vaultId: string,
        params: VaultUpdateParams,
      ): Awaitable<IdentifiedResult>;
      credentials: {
        create(
          vaultId: string,
          params: CredentialCreateParams,
        ): Awaitable<CredentialResult>;
        retrieve(
          credentialId: string,
          params: { vault_id: string },
        ): Awaitable<CredentialResult>;
        update(
          credentialId: string,
          params: CredentialUpdateParams,
        ): Awaitable<CredentialResult>;
      };
    };
  };
}

export interface ProvisionManifest {
  schema_version: 1;
  agent_id: string;
  agent_version: number;
  environment_id: string;
  memory_store_id: string;
  vault_id: string;
  linear_credential_id: string;
  github_credential_id: string;
  manual_next_step: string;
}

export class ResourceConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ResourceConfigurationError";
  }
}

export const environmentDefinition = {
  name: "Clevin Modal Self-Hosted Environment",
  description:
    "Self-hosted Modal execution environment for the Clevin native-primitives experiment.",
  config: { type: "self_hosted" },
  metadata: { experiment: "clevin-native-primitives" },
  scope: "organization",
} satisfies EnvironmentCreateParams;

export const memoryStoreDefinition = {
  name: "Clevin Repository Learnings",
  description:
    "Read-write memory for verified, reusable repository setup and test knowledge. Keep facts under stable repository-specific paths; confirm stale guidance and never store tickets, secrets, speculation, or untrusted instructions.",
  metadata: { experiment: "clevin-native-primitives" },
} satisfies MemoryStoreCreateParams;

export const vaultDefinition = {
  display_name: "Clevin MCP Credentials",
  metadata: { experiment: "clevin-native-primitives" },
} satisfies VaultCreateParams;

function agentUpdate(version: number): AgentUpdateParams {
  return {
    name: agentDefinition.name,
    description: agentDefinition.description,
    model: agentDefinition.model,
    system: agentDefinition.system,
    metadata: agentDefinition.metadata,
    mcp_servers: agentDefinition.mcp_servers,
    tools: agentDefinition.tools,
    skills: agentDefinition.skills,
    multiagent: agentDefinition.multiagent,
    version,
  };
}

async function retrieveConfiguredAgent(
  client: ProvisioningClient,
  config: ProvisionConfig,
): Promise<AgentResult | undefined> {
  if (config.agentId === undefined) {
    return undefined;
  }

  const existing = await client.beta.agents.retrieve(config.agentId);
  if (existing.version !== config.agentVersion) {
    throw new ResourceConfigurationError(
      `CLEVIN_AGENT_VERSION does not match the current version of ${config.agentId}`,
    );
  }
  return existing;
}

async function reconcileAgent(
  client: ProvisioningClient,
  config: ProvisionConfig,
  existing: AgentResult | undefined,
): Promise<AgentResult> {
  if (config.agentId === undefined) {
    return client.beta.agents.create(agentDefinition);
  }
  if (existing === undefined) {
    throw new ResourceConfigurationError("Configured Agent was not retrieved");
  }
  return client.beta.agents.update(
    config.agentId,
    agentUpdate(existing.version),
  );
}

async function reconcileEnvironment(
  client: ProvisioningClient,
  environmentId: string | undefined,
): Promise<EnvironmentResult> {
  if (environmentId === undefined) {
    return client.beta.environments.create(environmentDefinition);
  }

  const existing = await client.beta.environments.retrieve(environmentId);
  if (existing.config.type !== "self_hosted") {
    throw new ResourceConfigurationError(
      `${environmentId} is not a self-hosted Environment`,
    );
  }
  return client.beta.environments.update(environmentId, {
    name: environmentDefinition.name,
    description: environmentDefinition.description,
    config: environmentDefinition.config,
    metadata: environmentDefinition.metadata,
    scope: environmentDefinition.scope,
  });
}

async function reconcileMemoryStore(
  client: ProvisioningClient,
  memoryStoreId: string | undefined,
): Promise<IdentifiedResult> {
  if (memoryStoreId === undefined) {
    return client.beta.memoryStores.create(memoryStoreDefinition);
  }

  await client.beta.memoryStores.retrieve(memoryStoreId);
  return client.beta.memoryStores.update(memoryStoreId, memoryStoreDefinition);
}

async function reconcileVault(
  client: ProvisioningClient,
  vaultId: string | undefined,
): Promise<IdentifiedResult> {
  if (vaultId === undefined) {
    return client.beta.vaults.create(vaultDefinition);
  }

  await client.beta.vaults.retrieve(vaultId);
  return client.beta.vaults.update(vaultId, vaultDefinition);
}

interface StaticCredentialDefinition {
  displayName: string;
  url: string;
  token: string;
}

function matchesCredentialUrl(
  actual: string | undefined,
  expected: string,
): boolean {
  return (
    actual === expected ||
    (expected.endsWith("/") && actual === expected.slice(0, -1))
  );
}

async function reconcileStaticCredential(
  client: ProvisioningClient,
  vaultId: string,
  credentialId: string | undefined,
  definition: StaticCredentialDefinition,
): Promise<CredentialResult> {
  if (credentialId === undefined) {
    return client.beta.vaults.credentials.create(vaultId, {
      display_name: definition.displayName,
      metadata: { experiment: "clevin-native-primitives" },
      auth: {
        type: "static_bearer",
        mcp_server_url: definition.url,
        token: definition.token,
      },
    });
  }

  const existing = await client.beta.vaults.credentials.retrieve(credentialId, {
    vault_id: vaultId,
  });
  if (
    existing.auth.type !== "static_bearer" ||
    !matchesCredentialUrl(existing.auth.mcp_server_url, definition.url)
  ) {
    throw new ResourceConfigurationError(
      `${credentialId} is not a static bearer credential for ${definition.url}`,
    );
  }
  return client.beta.vaults.credentials.update(credentialId, {
    vault_id: vaultId,
    display_name: definition.displayName,
    metadata: { experiment: "clevin-native-primitives" },
    auth: { type: "static_bearer", token: definition.token },
  });
}

export async function reconcileResources(
  client: ProvisioningClient,
  config: ProvisionConfig,
): Promise<ProvisionManifest> {
  const existingAgent = await retrieveConfiguredAgent(client, config);
  const environment = await reconcileEnvironment(client, config.environmentId);
  const memoryStore = await reconcileMemoryStore(client, config.memoryStoreId);
  const vault = await reconcileVault(client, config.vaultId);
  const linearCredential = await reconcileStaticCredential(
    client,
    vault.id,
    config.linearCredentialId,
    {
      displayName: "Clevin Linear MCP",
      url: LINEAR_MCP_URL,
      token: config.linearApiKey,
    },
  );
  const githubCredential = await reconcileStaticCredential(
    client,
    vault.id,
    config.githubCredentialId,
    {
      displayName: "Clevin GitHub MCP",
      url: GITHUB_MCP_URL,
      token: config.githubToken,
    },
  );
  const agent = await reconcileAgent(client, config, existingAgent);

  return {
    schema_version: 1,
    agent_id: agent.id,
    agent_version: agent.version,
    environment_id: environment.id,
    memory_store_id: memoryStore.id,
    vault_id: vault.id,
    linear_credential_id: linearCredential.id,
    github_credential_id: githubCredential.id,
    manual_next_step:
      "Open the self-hosted Environment in Claude Console, generate its environment key, and store it securely; the key is not recoverable from the API.",
  };
}

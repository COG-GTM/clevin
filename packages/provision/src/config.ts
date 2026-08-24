import { fileURLToPath } from "node:url";
import { config as loadDotenv } from "dotenv";

const REPOSITORY_ENV_PATH = fileURLToPath(
  new URL("../../../.env", import.meta.url),
);

export interface ProvisionConfig {
  anthropicApiKey: string;
  githubToken: string;
  linearApiKey: string;
  agentId?: string;
  agentVersion?: number;
  environmentId?: string;
  vaultId?: string;
  linearCredentialId?: string;
  githubCredentialId?: string;
  memoryStoreId?: string;
}

export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

function requiredSecret(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name];
  if (value === undefined || value.trim() === "") {
    throw new ConfigurationError(`${name} is required`);
  }
  return value;
}

function optionalResourceId(
  env: NodeJS.ProcessEnv,
  name: string,
  prefix: string,
): string | undefined {
  const raw = env[name];
  if (raw === undefined || raw.trim() === "") {
    return undefined;
  }
  const value = raw.trim();
  if (!value.startsWith(prefix) || value.length === prefix.length) {
    throw new ConfigurationError(
      `${name} must be a valid ${prefix} resource ID`,
    );
  }
  return value;
}

function optionalAgentVersion(env: NodeJS.ProcessEnv): number | undefined {
  const raw = env.CLEVIN_AGENT_VERSION;
  if (raw === undefined || raw.trim() === "") {
    return undefined;
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new ConfigurationError(
      "CLEVIN_AGENT_VERSION must be a positive integer",
    );
  }
  return value;
}

export function parseProvisionConfig(env: NodeJS.ProcessEnv): ProvisionConfig {
  const agentId = optionalResourceId(env, "CLEVIN_AGENT_ID", "agent_");
  const agentVersion = optionalAgentVersion(env);
  if ((agentId === undefined) !== (agentVersion === undefined)) {
    throw new ConfigurationError(
      "CLEVIN_AGENT_ID and CLEVIN_AGENT_VERSION must be configured together",
    );
  }

  return {
    anthropicApiKey: requiredSecret(env, "ANTHROPIC_API_KEY"),
    githubToken: requiredSecret(env, "GITHUB_TOKEN"),
    linearApiKey: requiredSecret(env, "LINEAR_API_KEY"),
    ...(agentId === undefined ? {} : { agentId }),
    ...(agentVersion === undefined ? {} : { agentVersion }),
    ...optionalProperty(
      "environmentId",
      optionalResourceId(env, "CLEVIN_ENVIRONMENT_ID", "env_"),
    ),
    ...optionalProperty(
      "vaultId",
      optionalResourceId(env, "CLEVIN_VAULT_ID", "vlt_"),
    ),
    ...optionalProperty(
      "linearCredentialId",
      optionalResourceId(env, "CLEVIN_LINEAR_CREDENTIAL_ID", "vcrd_"),
    ),
    ...optionalProperty(
      "githubCredentialId",
      optionalResourceId(env, "CLEVIN_GITHUB_CREDENTIAL_ID", "vcrd_"),
    ),
    ...optionalProperty(
      "memoryStoreId",
      optionalResourceId(env, "CLEVIN_MEMORY_STORE_ID", "memstore_"),
    ),
  };
}

function optionalProperty<K extends keyof ProvisionConfig>(
  key: K,
  value: ProvisionConfig[K] | undefined,
): Partial<Pick<ProvisionConfig, K>> {
  return value === undefined
    ? {}
    : ({ [key]: value } as Pick<ProvisionConfig, K>);
}

export function loadProvisionConfig(
  env: NodeJS.ProcessEnv = process.env,
): ProvisionConfig {
  const dotenvEnv: Record<string, string> = {};
  for (const [name, value] of Object.entries(env)) {
    if (value !== undefined) {
      dotenvEnv[name] = value;
    }
  }
  const result = loadDotenv({
    path: REPOSITORY_ENV_PATH,
    processEnv: dotenvEnv,
    quiet: true,
  });
  if (
    result.error !== undefined &&
    "code" in result.error &&
    result.error.code !== "ENOENT"
  ) {
    throw new ConfigurationError("Unable to load the repository .env file");
  }
  return parseProvisionConfig(dotenvEnv);
}

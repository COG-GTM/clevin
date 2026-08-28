import { pathToFileURL } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import type {
  AgentRetrieveParams,
  BetaManagedAgentsAgent,
} from "@anthropic-ai/sdk/resources/beta/agents/agents";
import { agentDefinition } from "./agent-definition.js";
import { ConfigurationError, loadProvisionConfig } from "./config.js";
import { ResourceConfigurationError } from "./resources.js";

const MANAGED_FIELDS = [
  "name",
  "description",
  "model",
  "system",
  "metadata",
  "mcp_servers",
  "tools",
  "skills",
  "multiagent",
] as const;

export interface AgentDrift {
  path: string;
  desired: unknown;
  actual: unknown;
}

export function desiredAgentState(): Record<string, unknown> {
  const desired: Record<string, unknown> = {};
  for (const field of MANAGED_FIELDS) {
    desired[field] = structuredClone(agentDefinition[field]);
  }
  return desired;
}

export interface AgentDriftReport {
  drift: AgentDrift[];
  serverAdded: string[];
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function normalize(path: string, value: unknown): unknown {
  if (path === "model" && typeof value === "string") {
    return { id: value };
  }
  if (path === "model.effort" && typeof value === "string") {
    return { type: value };
  }
  return value;
}

function childPath(path: string, key: string): string {
  return path === "" ? key : `${path}.${key}`;
}

function arrayPath(path: string, index: number): string {
  return `${path}[${index}]`;
}

function diffValue(
  path: string,
  desired: unknown,
  actual: unknown,
  drift: AgentDrift[],
  serverAdded: string[],
): void {
  const normalizedDesired = normalize(path, desired);
  const normalizedActual = normalize(path, actual);

  if (Array.isArray(normalizedDesired) || Array.isArray(normalizedActual)) {
    if (!Array.isArray(normalizedDesired) || !Array.isArray(normalizedActual)) {
      drift.push({
        path,
        desired: normalizedDesired,
        actual: normalizedActual,
      });
      return;
    }
    if (normalizedDesired.length !== normalizedActual.length) {
      drift.push({
        path,
        desired: normalizedDesired,
        actual: normalizedActual,
      });
      return;
    }
    for (let index = 0; index < normalizedDesired.length; index += 1) {
      diffValue(
        arrayPath(path, index),
        normalizedDesired[index],
        normalizedActual[index],
        drift,
        serverAdded,
      );
    }
    return;
  }

  if (isPlainObject(normalizedDesired) || isPlainObject(normalizedActual)) {
    if (!isPlainObject(normalizedDesired) || !isPlainObject(normalizedActual)) {
      drift.push({
        path,
        desired: normalizedDesired,
        actual: normalizedActual,
      });
      return;
    }

    for (const key of Object.keys(normalizedDesired)) {
      const pathForKey = childPath(path, key);
      if (!Object.hasOwn(normalizedActual, key)) {
        drift.push({
          path: pathForKey,
          desired: normalizedDesired[key],
          actual: undefined,
        });
        continue;
      }
      diffValue(
        pathForKey,
        normalizedDesired[key],
        normalizedActual[key],
        drift,
        serverAdded,
      );
    }
    for (const key of Object.keys(normalizedActual)) {
      if (!Object.hasOwn(normalizedDesired, key)) {
        serverAdded.push(childPath(path, key));
      }
    }
    return;
  }

  if (!Object.is(normalizedDesired, normalizedActual)) {
    drift.push({
      path,
      desired: normalizedDesired,
      actual: normalizedActual,
    });
  }
}

export function diffAgentState(
  desired: Record<string, unknown>,
  actual: Record<string, unknown>,
): AgentDriftReport {
  const drift: AgentDrift[] = [];
  const serverAdded: string[] = [];
  diffValue("", desired, actual, drift, serverAdded);
  return { drift, serverAdded };
}

interface DriftClient {
  beta: {
    agents: {
      retrieve(
        agentId: string,
        params?: AgentRetrieveParams | null,
      ): PromiseLike<BetaManagedAgentsAgent>;
    };
  };
}

function actualAgentState(
  agent: BetaManagedAgentsAgent,
): Record<string, unknown> {
  return {
    name: agent.name,
    description: agent.description,
    model: agent.model,
    system: agent.system,
    metadata: agent.metadata,
    mcp_servers: agent.mcp_servers,
    tools: agent.tools,
    skills: agent.skills,
    multiagent: agent.multiagent,
  };
}

function parseVersion(args: string[]): number | undefined {
  const versionIndex = args.indexOf("--version");
  if (versionIndex === -1) {
    return undefined;
  }
  const rawVersion = args[versionIndex + 1];
  const version = Number(rawVersion);
  if (
    rawVersion === undefined ||
    !Number.isSafeInteger(version) ||
    version < 1
  ) {
    throw new ConfigurationError("--version must be a positive integer");
  }
  return version;
}

function loadDriftConfig() {
  try {
    return loadProvisionConfig();
  } catch (error: unknown) {
    if (
      !(error instanceof ConfigurationError) ||
      error.message !==
        "CLEVIN_AGENT_ID and CLEVIN_AGENT_VERSION must be configured together" ||
      process.env.CLEVIN_AGENT_VERSION !== undefined
    ) {
      throw error;
    }
    return loadProvisionConfig({
      ...process.env,
      CLEVIN_AGENT_VERSION: "1",
    });
  }
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const version = parseVersion(args);
  const config = loadDriftConfig();
  if (config.agentId === undefined) {
    throw new ConfigurationError("CLEVIN_AGENT_ID is required");
  }

  if (args.includes("--desired-only")) {
    process.stdout.write(
      `${JSON.stringify({ desired: desiredAgentState() })}\n`,
    );
    return;
  }

  const client: DriftClient = new Anthropic({
    apiKey: config.anthropicApiKey,
  });
  const agent = await client.beta.agents.retrieve(
    config.agentId,
    version === undefined ? undefined : { version },
  );
  const report = diffAgentState(desiredAgentState(), actualAgentState(agent));
  process.stdout.write(
    `${JSON.stringify({
      agent_id: agent.id,
      version: agent.version,
      ...report,
    })}\n`,
  );
  if (report.drift.length > 0) {
    process.exitCode = 1;
  }
}

function isExecutable(): boolean {
  const entrypoint = process.argv[1];
  return (
    entrypoint !== undefined &&
    import.meta.url === pathToFileURL(entrypoint).href
  );
}

if (isExecutable()) {
  main().catch((error: unknown) => {
    if (
      error instanceof ConfigurationError ||
      error instanceof ResourceConfigurationError
    ) {
      process.stderr.write(`${error.name}: ${error.message}\n`);
    } else {
      process.stderr.write("Drift detection failed\n");
    }
    process.exitCode = 1;
  });
}

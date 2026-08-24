import { pathToFileURL } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import {
  ConfigurationError,
  loadProvisionConfig,
  type ProvisionConfig,
} from "./config.js";
import {
  type ProvisioningClient,
  type ProvisionManifest,
  ResourceConfigurationError,
  reconcileResources,
} from "./resources.js";

export async function provision(
  client: ProvisioningClient,
  config: ProvisionConfig,
): Promise<ProvisionManifest> {
  return reconcileResources(client, config);
}

export function serializeManifest(manifest: ProvisionManifest): string {
  return JSON.stringify(manifest);
}

async function main(): Promise<void> {
  const config = loadProvisionConfig();
  const client = new Anthropic({ apiKey: config.anthropicApiKey });
  const manifest = await provision(client, config);
  process.stdout.write(`${serializeManifest(manifest)}\n`);
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
      process.stderr.write("Provisioning failed\n");
    }
    process.exitCode = 1;
  });
}

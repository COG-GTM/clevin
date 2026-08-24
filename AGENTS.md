# Clevin commands

- Install TypeScript dependencies: `pnpm install`
- Install Python dependencies: `uv sync --project runtime`
- Verify TypeScript: `pnpm verify`
- Verify Python: `uv run --project runtime ruff format --check runtime && uv run --project runtime ruff check runtime && uv run --project runtime mypy runtime/src && uv run --project runtime pytest -c runtime/pyproject.toml`
- Provision Anthropic resources: `pnpm --filter @clevin/provision provision`
- After provisioning, generate the Environment key manually in Claude Console and store it securely; it cannot be recovered from the API.
- Build the Modal Sandbox image: `uv run --project runtime modal run runtime/src/clevin_runtime/sandbox_image.py`
- Initialize the ignored Modal secret file with mode `0600`: `install -m 600 runtime/.env.example runtime/.modal-secret.env`
- Create or update the named Modal secret: `uv run --project runtime modal secret create clevin-runtime --from-dotenv runtime/.modal-secret.env --force`
- Deploy the Modal webhook: `uv run --project runtime modal deploy runtime/src/clevin_runtime/modal_app.py --env clevin`
- After registering the Modal URL as an Anthropic `session.status_run_started` webhook, add `ANTHROPIC_WEBHOOK_SECRET` to `runtime/.modal-secret.env`, rerun the secret-create command, and rerun the Modal deploy command.
- Start a ticket session: `uv run --project runtime clevin TICKET-ID`
- Resume a session: `uv run --project runtime clevin --resume SESSION-ID`

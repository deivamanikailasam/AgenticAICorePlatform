# How to add an MCP server

1. **Decide trust.** Is the server operated by us (or a vendor under contract) and reviewed?
   Only then set `trusted: true`. Untrusted servers' annotations are ignored.
2. **Add it to `platform.yaml`** under `mcp.servers`:

   ```yaml
   - name: crm                          # becomes mcp://crm/<tool>, model name crm__<tool>
     transport: http                    # or stdio
     url: https://crm.internal/mcp
     protocol: auto                     # auto | legacy | 2026-07-28
     trusted: false
     allowed_tools: [get_customer]      # expose only what agents need
     side_effects: { get_customer: read }
     auth:
       kind: oauth_client_credentials
       issuer: https://keycloak.internal/realms/rag-platform
       client_id: agent-platform
       client_secret: ${secret:keycloak/agent-platform#client_secret}
   ```

   For a stdio server, give `command` and `args`; pin package versions
   (`@modelcontextprotocol/server-filesystem@1.0.0`, never `@latest`).
3. **Store the secret** in OpenBao at the referenced path (Step 29; in dev, the
   `SECRET__…` environment variable).
4. **Use it in a spec**: `tools: [{ref: mcp://crm/get_customer}]`. The compiler refuses servers
   that are not listed.
5. **Check**: discover and call it once (`uv run pytest -m integration …` or a small script using
   `McpClientPool`), and look for `execute_tool get_customer` in Tempo.

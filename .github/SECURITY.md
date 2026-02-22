# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue** for security vulnerabilities
2. Use [GitHub Security Advisories](https://github.com/ideksec/NoScope/security/advisories/new) to report privately
3. Include steps to reproduce, impact assessment, and any suggested fixes

We will acknowledge receipt within 48 hours and aim to release a fix within 7 days for critical issues.

## Security Model

NoScope executes LLM-generated code on your machine. Understanding the security model is essential:

### Capability Gating
Every agent action requires an explicit capability grant. Before execution begins, NoScope shows which capabilities are needed (file access, shell execution, git, Docker) and requires your approval. Use `--yes` to auto-approve only in trusted environments.

### Safety Filters
Shell commands are checked against a deny-list of dangerous patterns (privilege escalation, destructive filesystem operations, crypto mining, reverse shells). The `--danger` flag bypasses all safety filters — use it only for trusted specs in isolated environments.

### Sandboxing
Use `--sandbox` to run agent commands inside a Docker container with:
- Memory and CPU limits
- No privilege escalation (`--no-new-privileges`)
- Workspace volume mount only

### Secret Handling
- API keys are loaded from environment variables or `.env` files
- Tool output is redacted for known secret patterns before logging
- Event logs (`events.jsonl`) contain tool arguments and results — treat them as sensitive

### Known Limitations
- **LLM prompt injection**: Spec content is passed directly to the LLM. A malicious spec could attempt to manipulate agent behavior. Only run specs you trust.
- **Regex-based safety filters**: The command deny-list uses pattern matching, which can be bypassed by determined adversaries. The sandbox provides stronger isolation.
- **No network isolation**: Without `--sandbox`, agent commands have full network access.

## Best Practices

1. **Review specs before running** — especially from untrusted sources
2. **Use `--sandbox`** for untrusted specs
3. **Never use `--danger`** with untrusted specs
4. **Use short timeboxes** to limit exposure
5. **Review event logs** after runs for unexpected activity
6. **Keep API keys in `.env`** (gitignored) rather than environment exports

# Configuration

`infra-builder` reads and writes UTF-8 YAML with `schema_version: 1`.
Unknown fields and unsupported schema versions are rejected. The
configuration describes a local target only; it does not cause
infrastructure to be created or services to be started.

## Example

```yaml
schema_version: 1
project:
  name: demo-api
  output_dir: ./generated/demo-api
infrastructure:
  target: local
  os: ubuntu
runtime:
  type: docker_compose
application:
  type: generic
  name: api
  port: 8080
  image: nginx:stable
services:
  postgres:
    enabled: true
    database: app
    username: app_user
    password_env: INFRA_BUILDER_POSTGRES_PASSWORD
  redis:
    enabled: false
observability:
  metrics: true
  centralized_logs: false
execution:
  require_approval: true
```

## Supported values

- `infrastructure.target`: `local`
- `runtime.type`: `docker_compose` or `host_systemd`
- `application.type`: `generic`, `python`, `go`, or `static`
- `application.port`: integer from 1 through 65535
- Project, application, database, and username names: lowercase letters,
  digits, and hyphens; begin with a lowercase letter
- `project.output_dir`: relative path without parent-directory (`..`)
  segments; validation also rejects paths that resolve through a symlink
  outside the directory containing the config
- Docker Compose configurations require an application image
- `services.postgres.password_env`: environment variable name only, never
  a password value

The `execution.require_approval` setting is fixed to `true`. Unsupported
targets, runtimes, extra keys, embedded secrets, and schema versions are
not accepted by the schema.

## Commands

`infra-builder init` launches the interactive wizard and writes
`infra.yaml`. `infra-builder init --non-interactive` writes the safe
defaults. Use `--output PATH` to select another config file location.
`infra-builder validate [CONFIG]` validates the file (default:
`infra.yaml`) and makes no system changes.

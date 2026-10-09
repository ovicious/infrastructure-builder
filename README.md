# Infrastructure Builder

Infrastructure Builder (`infra-builder`) is a safety-first CLI for
planning and generating local infrastructure. The current 0.1.0
milestone provides configuration initialization, validation,
deterministic planning, and local file generation. It does not configure
hosts, create infrastructure, or run services.

## Requirements

- Python 3.11 or newer

## Install for development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## CLI

```bash
infra-builder --help
infra-builder version
infra-builder init
infra-builder validate infra.yaml
infra-builder plan infra.yaml
infra-builder generate infra.yaml
```

Use `infra-builder init --non-interactive` to write the safe default
configuration, or pass `--output PATH` to choose where the YAML file is
saved. The interactive wizard supports local Docker Compose and
generate-only host-systemd configuration choices.

Configuration fields and validation rules are documented in
[docs/configuration.md](docs/configuration.md).

`plan` lists generated files without writing anything; use
`--format json` for machine-readable output. `generate` writes only
inside the configured project output directory (or the relative path
given with `--output`). Generated files are recorded in a hash manifest.
It refuses to overwrite unmanaged files, manually changed managed files,
or unsafe symlink targets. Generation does not run Docker or systemd.
Generated Compose ports are bound to localhost by default.

## Safety and scope

Initialization, validation, and planning do not execute commands or
modify hosts. Generation creates reviewable files only; generated
systemd units are templates and are never installed or activated.
Future execution will be reviewed before any explicit, approval-gated
apply step. AI will remain optional and will never be allowed to execute
commands.

See [docs/roadmap.md](docs/roadmap.md) for the planned milestones.

## License

Licensed under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).
Commercial use is not permitted under this license. Review the complete
license terms before using, modifying, or distributing this project.

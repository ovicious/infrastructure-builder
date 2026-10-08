# Infrastructure Builder

Infrastructure Builder (`infra-builder`) is a safety-first CLI for
planning and generating local infrastructure. It is being built in
milestones; the current 0.1.0 release is the project skeleton only.
It does not configure hosts, create infrastructure, or run services.

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
```

## Safety and scope

This release includes no infrastructure execution. Future operations
will be planned and reviewed before any explicit, approval-gated apply
step. AI will remain optional and will never be allowed to execute
commands.

See [docs/roadmap.md](docs/roadmap.md) for the planned milestones.

## License

Licensed under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).
Commercial use is not permitted under this license. Review the complete
license terms before using, modifying, or distributing this project.

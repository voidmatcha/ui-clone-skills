# ui-clone-skills — convenience entry points.
# Most automation lives in scripts/ci/ and skills/<name>/scripts/.
# This Makefile only collects the human-facing top-level commands.
#
# To run the regression benchmark: open a Claude Code session with
# `claude --plugin-dir $(pwd)` and trigger the `benchmark` skill by
# typing "run benchmark". The skill (skills/benchmark/SKILL.md) drives
# the entire pipeline LLM-side — Python only verifies + harvests.

.PHONY: ci security help

help:
	@echo "ui-clone-skills targets:"
	@echo "  make ci          Run scripts/ci/ci-local.sh (mirror of the GHA test job)"
	@echo "  make security    Run scripts/ci/pre-push-security.sh"
	@echo
	@echo "Benchmark: open Claude Code with \`claude --plugin-dir \$$(pwd)\` and"
	@echo "type \"run benchmark\". The benchmark skill drives the pipeline LLM-side"
	@echo "and is the canonical regression entry point."

ci:
	bash scripts/ci/ci-local.sh

security:
	bash scripts/ci/pre-push-security.sh

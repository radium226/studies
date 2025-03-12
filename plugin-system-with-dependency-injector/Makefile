#!/usr/bin/make -f

SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:

.DEFAULT_GOAL := all

.PHONY: all
all: check

.PHONY: check
check:
	# data-platforrm-core-spi
	uv run \
		--directory="./packages/core/spi" \
		--package="data-platform-core-spi" \
			mypy \
				"./src"
	uv run \
		--directory="./packages/core/spi" \
		--package="data-platform-core-spi" \
			ruff \
				check \
					"./src"

	# data-platforrm-core-di
	uv run \
		--directory="./packages/core/di" \
		--package="data-platform-core-di" \
			mypy \
				"./src"

	uv run \
		--directory="./packages/core/di" \
		--package="data-platform-core-di" \
			ruff \
				check \
					"./src"
	
	# data-platforrm-core-cli
	uv run \
		--directory="./packages/core/cli" \
		--package="data-platform-core-cli" \
			mypy \
				"./src"

	uv run \
		--directory="./packages/core/cli" \
		--package="data-platform-core-cli" \
			ruff \
				check \
					"./src"

	# data-platform-tools-slack
	uv run \
		--directory="./packages/tools/slack" \
		--package="data-platform-tools-slack" \
			mypy \
				"./src"

	uv run \
		--directory="./packages/tools/slack" \
		--package="data-platform-tools-slack" \
			ruff \
				check \
					"./src"

	# data-platform-exports-sales
	uv run \
		--directory="./packages/exports/sales" \
		--package="data-platform-exports-sales" \
			mypy \
				"./src"

	uv run \
		--directory="./packages/exports/sales" \
		--package="data-platform-exports-sales" \
			ruff \
				check \
					"./src"
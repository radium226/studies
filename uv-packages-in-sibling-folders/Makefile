#!/usr/bin/make -f

SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:

.DEFAULT_GOAL := all

CACHE := yes


.PHONY: all
all: build-module run-module

.PHONY: build-module
build-module: guard-MODULE
	@ echo "Building module: ${MODULE}! " >&2

	docker \
		build \
			$(shell if [[ "$(CACHE)" == "no" ]]; then echo "--no-cache"; fi) \
			--progress="plain" \
			--tag "$(MODULE)" \
			--build-arg MODULE="$(MODULE)" \
			--cache-to type=local,dest="./cache" \
  			--cache-from type=local,src="./cache" \
			--file "./modules/${MODULE}/docker/Dockerfile" \
				"."
	

.PHONY: $(filter guard-%,$(MAKECMDGOALS))
guard-%:
	@ if [[ -z "$($*)" ]]; then
		echo "The $* variable is not set!" >&2 
		exit 1
	fi


.PHONY: run-module
run-module: guard-MODULE
	@ echo "Running module: ${MODULE}! " >&2

	docker \
		run \
			--rm \
			--name "$(MODULE)" \
				"$(MODULE)"
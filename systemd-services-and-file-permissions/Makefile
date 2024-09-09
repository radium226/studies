#!/usr/bin/make -f

SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


.PHONY: start-vm
start-vm:
	vagrant up --no-provision --no-tty

.PHONY: provision-vm
provision-vm:
	vagrant provision --no-tty

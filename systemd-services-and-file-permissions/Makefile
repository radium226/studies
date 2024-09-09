#!/usr/bin/make -f

SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


.PHONY: start-vm
start-vm:
	vagrant up \
		--provision-with "install-python" \
		--no-tty

.PHONY: stop-vm
stop-vm:
	vagrant destroy --force

.PHONY: provision-vm
provision-vm:
	VAGRANT_LOG=warn vagrant provision \
		--provision-with "run-ansible-playbook"

.PHONY: shell
shell:
	vagrant ssh
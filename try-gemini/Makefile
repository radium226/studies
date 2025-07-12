SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


PUBLIC_KEY := $(shell cat "./public.key")
PRIVATE_KEY := $(shell cat "./private.key")


.DEFAULT_GOAL := demo


.PHONY: demo
demo:
	@ make/target demo 
 



SECRET_VARIABLE_PATTERN := API_KEY|API_TOKEN|SECRET|PASSWORD|CREDENTIALS|PRIVATE


.PHONY: encrypt-dotenv
encrypt-dotenv:
	SOPS_AGE_RECIPIENTS=$(PUBLIC_KEY) \
		sops \
			--encrypt \
			--input-type "dotenv" \
			--output-type "dotenv" \
			--encrypted-regex='$(SECRET_VARIABLE_PATTERN)' \
			--in-place \
				"./variables.env"
	

.PHONY: decrypt-dotenv
decrypt-dotenv:
	SOPS_AGE_KEY="$(PRIVATE_KEY)" \
		sops \
			--decrypt \
			--input-type "dotenv" \
			--output-type "dotenv" \
			--in-place \
				"./variables.env"


.PHONY: edit-dotenv
edit-dotenv:
	SOPS_AGE_KEY="$(PRIVATE_KEY)" \
		sops \
			edit \
				"./variables.env"
#!/usr/bin/make -f

SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


.PHONY: install
install:
	install \
		-D \
		-g "root" -o "root" -m "u=rwx,g=rx,o=rx" \
		-t "/usr/local/bin" \
			"hello" "wait-for-port" "debug-socket"
	
	install \
		-D \
		-g "root" -o "root" -m "u=rw,g=r,o=" \
		-t "/usr/local/lib/systemd/system" \
			"hello.service" "proxy-to-hello.service" "proxy-to-hello.socket" "debug-socket@.service" "debug-socket.socket"

	systemctl daemon-reload

	systemctl enable --now "proxy-to-hello.socket"
	systemctl enable --now "debug-socket.socket"
	systemctl restart "debug-socket.socket"
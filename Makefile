SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


BUILDDIR := ./build


.PHONY: test
test: install
	sudo iptables -P FORWARD ACCEPT
	sudo systemctl restart \
		"vpn-passthrough-netns.service" \
		"vpn-passthrough-connectivity.service" \
		"vpn-passthrough-internet.service" \
		"vpn-passthrough-pia.service"
	sudo ip netns exec "pia" ping -c 1 -W 1 "www.google.fr"
	sudo systemctl restart "vpn-passthrough-pia.service"

	dummy-app show-ip
	sleep 5
	sudo systemd-run \
		--property "NetworkNamespacePath=/var/run/netns/pia" \
		--property "BindReadOnlyPaths=/etc/netns/pia/resolv.conf:/etc/resolv.conf:norbind" \
		--pty \
		--wait \
		--collect \
		--service-type="exec" \
			dummy-app \
				show-ip
	


.PHONY: install
install: build
	find "$(BUILDDIR)" -maxdepth 1 -type "f" -name "*.pkg.tar.zst" -exec sudo pacman -U --noconfirm "{}" \;


.PHONY: build
build: build-dummy-app build-vpn-passthrough


.PHONY: build-dummy-app
build-dummy-app:
	mkdir -p "$(BUILDDIR)/dummy-app"
	cp "./dummy-app/PKGBUILD.in" "$(BUILDDIR)/dummy-app/PKGBUILD"
	pdm build --project="./dummy-app" --no-wheel --no-clean --dest $(shell realpath -m --relative-to "./dummy-app" "$(BUILDDIR)/dummy-app")
	cd "$(BUILDDIR)/dummy-app"
	makepkg --force --skipchecksums
	cd -
	cp "$(BUILDDIR)/dummy-app/dummy-app-0.1.0-1-any.pkg.tar.zst" "$(BUILDDIR)"


.PHONY: build-vpn-passthrough
build-vpn-passthrough:
	mkdir -p "$(BUILDDIR)/vpn-passthrough"
	cp "./vpn-passthrough/PKGBUILD.in" "$(BUILDDIR)/vpn-passthrough/PKGBUILD"
	find "./vpn-passthrough" -type "f" ! -name "PKGBUILD.in" ! -name "pia.txt.in" -exec cp "{}" "$(BUILDDIR)/vpn-passthrough" \;
	sed -r \
		-e "s,%\{PIA_USER\}%,${PIA_USER},g" \
		-e "s,%\{PIA_PASSWORD\}%,${PIA_PASSWORD},g" \
			./vpn-passthrough/pia.txt.in \
			>"$(BUILDDIR)/vpn-passthrough/pia.txt"
	
	cd "$(BUILDDIR)/vpn-passthrough"
	makepkg --force --skipchecksums
	cd -
	cp "$(BUILDDIR)/vpn-passthrough/vpn-passthrough-0.1.0-1-any.pkg.tar.zst" "$(BUILDDIR)"
	

.PHONY: clean
clean:
	rm -Rf "$(BUILDDIR)"


.PHONY: list-pia-regions
list-pia-regions:
	curl \
		-X "GET" \
		-H "Accept: application/json" \
			"https://serverlist.piaservers.net/vpninfo/servers/v6" | \
	head -n1 | \
		jq -r '.regions[].id' | \
	sort

.PHONY: show-pia-region
show-pia-region:
	curl \
		-X "GET" \
		-H "Accept: application/json" \
			"https://serverlist.piaservers.net/vpninfo/servers/v6" | \
	head -n1 | \
		jq '.regions[] | select(.id == "$(REGION)") | .servers.ovpnudp[0]'

# BUILDDIR := ./build
# DESTDIR := /usr/local
# DISTDIR := ./dist
# PREFIX := $(DESTDIR)

# VERSION := 0.0.1
# RELEASE := 1


# SOURCE_FILES := $(shell find "$(SRCDIR)/scripts" "$(SRCDIR)/functions" "$(SRCDIR)/systemd-units" -type "f")
# BUILD_FILES := $(SOURCE_FILES:$(SRCDIR)/%=$(BUILDDIR)/%)


# build: $(BUILD_FILES)


# $(BUILDDIR)/scripts/%: $(SRCDIR)/scripts/%
# 	mkdir -p "$(BUILDDIR)/scripts"
# 	sed \
# 		-r\
# 		-e "s,%\{PREFIX\},$(PREFIX),g" \
# 		"$<" >"$@"


# $(BUILDDIR)/%: $(SRCDIR)/%
# 	mkdir -p "$(shell dirname "$@" )"
# 	cp "$<" "$@"


# .PHONY: install
# install:
# 	# Install functions
# 	find "$(BUILDDIR)/functions" -type "f" -exec install \
# 		-D \
# 		-m "u=rw,g=r,o=" \
# 		-t "$(DESTDIR)/lib/encrypted-storage/functions" \
# 			"{}" \;

# 	# Install binaries
# 	install \
# 		-D \
# 		-m "u=rwx,g=rx,o=x" \
# 		-t "$(DESTDIR)/bin" \
# 			"$(BUILDDIR)/scripts/encrypted-storage" \
# 			"$(BUILDDIR)/scripts/mount.encrypted-storage" \
# 			"$(BUILDDIR)/scripts/umount.encrypted-storage"

# 	# Install systemd generator
# 	install \
# 		-D \
# 		-m "u=rwx,g=rx,o=x" \
# 		-t "$(DESTDIR)/lib/systemd/system-generators" \
# 			"$(BUILDDIR)/scripts/systemd-encrypted-storage-generator"

# 	# Install systemd units
# 	install \
# 		-D \
# 		-m "u=rw,g=r,o=" \
# 		-t "$(DESTDIR)/lib/systemd/system" \
# 			"$(BUILDDIR)/systemd-units/encrypted-storage.target"


# .PHONY: uninstall
# uninstall:
# 	rm "$(DESTDIR)/lib/systemd/system/encrypted-storage.target"
# 	rm "$(DESTDIR)/lib/systemd/system-generators/systemd-encrypted-storage-generator"
# 	rm "$(DESTDIR)/bin/umount.encrypted-storage"
# 	rm "$(DESTDIR)/bin/mount.encrypted-storage"
# 	rm "$(DESTDIR)/bin/encrypted-storage"
# 	rm -Rf "$(DESTDIR)/lib/encrypted-storage"


# .PHONY: enable
# enable:
# 	systemctl daemon-reload
# 	systemctl enable --now "encrypted-storage.target"


# .PHONY: clean
# clean:
# 	trash "$(BUILDDIR)"
# 	trash "encrypted-storage-$(VERSION).tar.gz"

# $(DISTDIR)/encrypted-storage-$(VERSION).tar.gz:
# 	mkdir -p "$(DISTDIR)"
# 	tar -cf "$(DISTDIR)/encrypted-storage-$(VERSION).tar.gz" "Makefile" "src"

# $(DISTDIR)/PKGBUILD: PKGBUILD
# 	cp "PKGBUILD" "$(DISTDIR)/PKGBUILD"

# .PHONY: package
# package: $(DISTDIR)/encrypted-storage-$(VERSION).tar.gz $(DISTDIR)/PKGBUILD
# 	cd "$(DISTDIR)"

# 	PKGVER="$(VERSION)" \
# 	PKGREL="$(RELEASE)" \
# 		makepkg \
# 			--force \
# 			--skipchecksums \
# 			--nodeps

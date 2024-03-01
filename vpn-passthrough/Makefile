SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


BUILDDIR := ./build


.PHONY: test
test: install
	sudo sysctl -w net.ipv4.ip_forward=1
	# sudo iptables -F
	# sudo iptables -P INPUT ACCEPT
	# sudo iptables -P OUTPUT ACCEPT
	sudo iptables -P FORWARD ACCEPT

	sudo mkdir -p "/etc/vpn-passthrough"

	sudo cp "./tests/dummy-app-foo.yaml" "/etc/vpn-passthrough/dummy-app-foo.yaml"
	sudo cp "./tests/dummy-app-bar.yaml" "/etc/vpn-passthrough/dummy-app-bar.yaml"
	
	sudo systemctl stop \
		"vpn-passthrough-netns@dummy-app-foo" \
		"vpn-passthrough-veth@dummy-app-foo" \
		"vpn-passthrough-forwarding@dummy-app-foo" \
		"vpn-passthrough-pia@dummy-app-foo" \
		"vpn-passthrough-configuration@dummy-app-foo" || true

	sudo systemctl stop \
		"vpn-passthrough-netns@dummy-app-bar" \
		"vpn-passthrough-veth@dummy-app-bar" \
		"vpn-passthrough-forwarding@dummy-app-bar" \
		"vpn-passthrough-pia@dummy-app-bar" \
		"vpn-passthrough-configuration@dummy-app-bar" || true

	sudo systemctl start \
		"vpn-passthrough-netns@dummy-app-foo" \
		"vpn-passthrough-veth@dummy-app-foo" \
		"vpn-passthrough-forwarding@dummy-app-foo" \
		"vpn-passthrough-pia@dummy-app-foo" \
		"vpn-passthrough-configuration@dummy-app-foo" || true

	sudo systemctl start \
		"vpn-passthrough-netns@dummy-app-bar" \
		"vpn-passthrough-veth@dummy-app-bar" \
		"vpn-passthrough-forwarding@dummy-app-bar" \
		"vpn-passthrough-pia@dummy-app-bar" \
		"vpn-passthrough-configuration@dummy-app-bar" || true

	sleep 2.5

	sudo ip netns exec "dummy-app-foo" ping -c 1 -W 1 "www.google.fr"

	sudo ip netns exec "dummy-app-bar" ping -c 1 -W 1 "www.google.fr"

	sudo mkdir -p "/etc/systemd/system/dummy-app@foo.service.d"
	sudo cp "./tests/dummy-app-foo-vpn-passthrough.conf" "/etc/systemd/system/dummy-app@foo.service.d/vpn-passthrough.conf"

	sudo mkdir -p "/etc/systemd/system/dummy-app@bar.service.d"
	sudo cp "./tests/dummy-app-bar-vpn-passthrough.conf" "/etc/systemd/system/dummy-app@bar.service.d/vpn-passthrough.conf"

	sudo systemctl daemon-reload
	sudo systemctl restart "dummy-app@foo.service" "dummy-app@bar.service"

	dummy-app show-ip

	sleep 2.5
	sudo systemd-run \
		--property "NetworkNamespacePath=/var/run/netns/dummy-app-foo" \
		--property "BindReadOnlyPaths=/etc/netns/dummy-app-foo/resolv.conf:/etc/resolv.conf:norbind" \
		--pty \
		--wait \
		--collect \
		--service-type="exec" \
			dummy-app \
				show-ip

	sudo systemd-run \
		--property "NetworkNamespacePath=/var/run/netns/dummy-app-bar" \
		--property "BindReadOnlyPaths=/etc/netns/dummy-app-bar/resolv.conf:/etc/resolv.conf:norbind" \
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
	cp "./dummy-app/dummy-app@.service" "$(BUILDDIR)/dummy-app"
	
	cd "$(BUILDDIR)/dummy-app"
	makepkg --force --skipchecksums
	cd -
	cp "$(BUILDDIR)/dummy-app/dummy-app-0.1.0-1-any.pkg.tar.zst" "$(BUILDDIR)"


.PHONY: build-vpn-passthrough
build-vpn-passthrough:
	# Let's create the build folder
	mkdir -p "$(BUILDDIR)/vpn-passthrough"

	# Copy the PKGBUILD.in to PKGBUILD
	cp "./vpn-passthrough/PKGBUILD.in" "$(BUILDDIR)/vpn-passthrough/PKGBUILD"
	pdm build --project="./vpn-passthrough/app" --no-wheel --no-clean --dest $(shell realpath -m --relative-to "./vpn-passthrough/app" "$(BUILDDIR)/vpn-passthrough")
	
	# Copy the other files (and populate pia.txt)
	find "./vpn-passthrough" -maxdepth 1 -type "f" ! -name "PKGBUILD.in" -exec cp "{}" "$(BUILDDIR)/vpn-passthrough" \;
	sed -r \
		-e "s,%\{PIA_USER\}%,${PIA_USER},g" \
		-e "s,%\{PIA_PASSWORD\}%,${PIA_PASSWORD},g" \
			"./vpn-passthrough/pia.txt.in" \
				>"$(BUILDDIR)/vpn-passthrough/pia.txt"

	# Make the package
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
		jq '.regions[] | select(.id == "$(REGION)")'
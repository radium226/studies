SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


BUILDDIR ?= ./build
SRCDIR ?= .


.PHONY: build
build:
	# Let's create the build folder
	mkdir -p "$(BUILDDIR)"

	# Copy the PKGBUILD.in to PKGBUILD
	cp "$(SRCDIR)/PKGBUILD.in" "$(BUILDDIR)/PKGBUILD"
	pdm build --project="$(SRCDIR)/app" --no-wheel --no-clean --dest $(shell realpath -m --relative-to "$(SRCDIR)/app" "$(BUILDDIR)")
	
	# Copy the other files (and populate pia.txt)
	find "$(SRCDIR)" -maxdepth 1 -type "f" ! -name "PKGBUILD.in" -exec cp "{}" "$(BUILDDIR)" \;
	sed -r \
		-e "s,%\{PIA_USER\}%,${PIA_USER},g" \
		-e "s,%\{PIA_PASSWORD\}%,${PIA_PASSWORD},g" \
			"$(SRCDIR)/pia.txt.in" \
				>"$(BUILDDIR)/pia.txt"

	# Make the package
	cd "$(BUILDDIR)"
	makepkg --force --skipchecksums
	cd -


.PHONY: clean
clean:
	rm -Rf "$(BUILDDIR)"
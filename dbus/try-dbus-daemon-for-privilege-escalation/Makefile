# Makefile for radium226-run D-Bus daemon

.PHONY: install uninstall check-root

PKG_NAME = radium226-run
PKG_VERSION = 0.1.0
SOURCE_NAME = radium226_run

check-root:
	@if [ "$$(id -u)" -ne 0 ]; then \
		echo "Error: This target requires root privileges. Run with sudo."; \
		exit 1; \
	fi

install:
	@echo "Building source tarball and installing package..."
	
	# Create source tarball using uv
	@echo "Creating source tarball..."
	uv build --sdist
	
	# Prepare build directory with all necessary files
	@echo "Preparing source for PKGBUILD..."
	mkdir -p /tmp/$(PKG_NAME)-build
	cp dist/$(SOURCE_NAME)-$(PKG_VERSION).tar.gz /tmp/$(PKG_NAME)-build/$(PKG_NAME)-$(PKG_VERSION).tar.gz
	cp PKGBUILD /tmp/$(PKG_NAME)-build/
	cp $(PKG_NAME).install /tmp/$(PKG_NAME)-build/
	
	# Extract source tarball and rename directory to match PKGBUILD expectations
	@echo "Extracting source for PKGBUILD access..."
	cd /tmp/$(PKG_NAME)-build && tar -xzf $(PKG_NAME)-$(PKG_VERSION).tar.gz
	cd /tmp/$(PKG_NAME)-build && mv $(SOURCE_NAME)-$(PKG_VERSION) $(PKG_NAME)-$(PKG_VERSION)
	
	# Build and install package
	@echo "Building and installing package with makepkg..."
	cd /tmp/$(PKG_NAME)-build && makepkg -si --noconfirm
	
	# Clean up
	rm -rf /tmp/$(PKG_NAME)-build
	
	@echo "✅ Package installed successfully!"

uninstall: check-root
	@echo "Uninstalling $(PKG_NAME) package..."
	pacman -R $(PKG_NAME) --noconfirm
	@echo "✅ Package uninstalled successfully!"
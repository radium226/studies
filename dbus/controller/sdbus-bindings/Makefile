SHELL := bash
.SHELLFLAGS := -euEo pipefail -c

.ONESHELL:


.PHONY: clean
clean:
	@ echo "Cleaning the project... " >&2
	rm -rf "./build"


.PHONY: build
build:
	@ echo "Building the project: " >&2
	mkdir -p "./build"
	
	echo " - Generating SWIG interface..." >&2
	swig -python -o "./build/sd_bus_prout.c" "./src/sd_bus/sd_bus_prout.i"

	echo " - Compiling C code... " >&2
	cd "./build"
	gcc \
		-Wall \
		-std=c99 \
		-c \
		"../src/sd_bus/sd_bus.c" \
		"./sd_bus_prout.c" \
		-I "/usr/include/python3.13" \
		-I "../src/sd_bus" \
		-fPIC
	cd - >"/dev/null"

	echo " - Linking shared library..." >&2
	gcc -shared -o "./build/_sd_bus_prout.so" "./build/sd_bus_prout.o" "./build/sd_bus.o"

	echo " - Move everything back to src/... " >&2
	cp "./build/_sd_bus_prout.so" "./src/sd_bus/"
	cp "./build/sd_bus_prout.py" "./src/sd_bus/"
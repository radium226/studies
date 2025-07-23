#!/usr/bin/env bash

trap 'echo "coucou"; exit 42' EXIT

i=0
while true; do
    echo "Bip! ${i}"
    echo "Bop! ${i}" >&2
    sleep 0.1
    i=$((i + 1))
done
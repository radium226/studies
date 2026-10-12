"""One module per backend, imported directly (`from .backends.cpu import CPUBackend`).

Nothing is re-exported here on purpose: importing `gpu` loads PyNvVideoCodec,
which may fail on machines without the NVIDIA driver, where the CPU backend
must still work.
"""

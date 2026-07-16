Structural issues in the pipeline

1. Unbounded channels = no backpressure. Every Channel() defaults to max_size=0, so produce_frames will read the entire source into memory as fast as the source allows — the smoke run showed all frames queued before the detector even started. With real video frames (multi-MB numpy arrays) this is a memory blow-up. Bounded channels (e.g. a small max_size wired into PipelineConfig, say a channels.capacity knob) would make the producer naturally pace itself against the slowest stage.
2. The service ABCs are synchronous but called on the event loop. read_frame(), detect_faces(), embed_faces(), write_frame() all block the loop for their full duration. For fakes that's invisible; for real ONNX inference or ffmpeg pipe reads it stalls every other task (the parent app offloads inference to a thread executor precisely for this). Either make the ABCs async (implementations decide how to offload), or wrap the calls in loop.run_in_executor. This is the biggest gap between the kernel's design and its intended use.
3. The annotation work is thrown away at the sink. render_frames carefully computes bracket, is_exact, flushed — and then consume_frames writes only annotated_frame.frame. Nothing outside the pipeline ever sees the detections. Presumably a renderer/overlay step is still to come, but as it stands FrameSink should probably accept the AnnotatedFrame (or there should be a FrameRenderer service between them). Related: FrameBroadcaster is defined and exported but used nowhere.
4. Channel close semantics are fragile. The None sentinel means you can never send a None-able item type, send() after close() silently works, and only one consumer ever sees the sentinel. You're on Python 3.13, which added Queue.shutdown() / QueueShutDown for exactly this — it would remove the sentinel, support multiple consumers, and make send-after-close raise.

Config

5. Defaults are declared twice. max_frames: int = 4 in the dataclass and _read_int(mapping, "max_frames", 4, ...) in from_dict — they will drift. Two clean fixes: only pass keys that are present (kwargs = {k: parse(v) for k, v in mapping.items()} and let the dataclass defaults apply), or derive the whole from_dict generically from fields(cls) introspection. Alternatively, admit this is schema validation and use pydantic — it replaces all of _require_mapping/_read_*/_reject_unknown_keys (model_config = ConfigDict(extra="forbid"), Field(ge=1)) at the cost of a heavier dependency. For a study project the hand-rolled version is defensible, but it grows linearly with every field you add.
6. from_yaml_file doesn't wrap I/O errors — a missing file raises raw FileNotFoundError while everything else raises PipelineConfigError; callers can't catch one exception type.

Logging

7. Hot-path TRACE args are evaluated eagerly. Loguru defers formatting, but self.queue.qsize(), len(...) etc. are computed on every call even when TRACE is off. It's cheap here, but per-frame per-channel it adds up; logger.opt(lazy=True) with callables is the loguru idiom if it ever shows in a profile.
8. Library etiquette: the kernel should ship with logger.disable("video_analyzer.kernel") in its top-level __init__ so consumers opt in — right now any app importing it gets DEBUG chatter on stderr by default.

Smaller things

- detect_batch reads batch_gate.max_batch_frames to sample — it should read self.config.batching.max_frames directly instead of reaching through a collaborator for configuration it already owns.
- find_segment is a linear scan per pending frame; snapshots are sorted by frame_index, so bisect would do. Pruning keeps the list small, so this is theoretical until lookahead gets large.
- Test coverage is thin outside config: nothing exercises BatchGate timing (a FakeClock you control would make lag/token-bucket behavior testable), Channel, sample_evenly edge cases, scene-cut flushing, or lookahead gating.
- pyproject.toml still says name = "core" and "Add your description here" — worth fixing before anything imports this as a real dependency.

If I had to pick three: bounded channels (1), async service interfaces (2), and the sink dropping annotations (3) — those change the architecture's fitness for the streaming use case, while the rest is polish. Want me to tackle any of these?


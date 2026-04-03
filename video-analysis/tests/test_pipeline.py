from pathlib import Path

from video_analysis import (
    Detection,
    Frames,
    SceneInfo,
    TextInfo,
    detect,
    drain,
    scenes_detector,
    stub_texts_detector,
)


async def test_nested_detection(multi_scene_video: Frames) -> None:
    """Detect scenes, then detect text within each scene."""
    all_texts: list[Detection[TextInfo]] = []

    async for scene in detect(multi_scene_video, use=scenes_detector()):
        async for text in detect(scene.frames, use=stub_texts_detector(every=5)):
            all_texts.append(text)

    assert len(all_texts) > 0
    for text_det in all_texts:
        assert text_det.metadata.text.startswith("text@")


async def test_scene_write(multi_scene_video: Frames, tmp_path: Path) -> None:
    """Write each scene to a separate file."""
    async for scene in detect(multi_scene_video, use=scenes_detector()):
        out = tmp_path / f"scene_{scene.metadata.index}.mp4"
        await scene.write(out)
        assert out.exists()
        assert out.stat().st_size > 0


async def test_drain(sample_video: Frames) -> None:
    """drain() consumes all frames without error."""
    await drain(sample_video)

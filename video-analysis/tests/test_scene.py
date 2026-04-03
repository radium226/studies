from video_analysis import Detection, Frames, SceneInfo, detect, scenes_detector


async def test_single_scene(sample_video: Frames) -> None:
    scenes: list[Detection[SceneInfo]] = []
    async for scene in detect(sample_video, use=scenes_detector()):
        scenes.append(scene)

    assert len(scenes) == 1
    assert scenes[0].metadata.index == 0


async def test_multiple_scenes(multi_scene_video: Frames) -> None:
    scenes: list[Detection[SceneInfo]] = []
    async for scene in detect(multi_scene_video, use=scenes_detector(threshold=0.95)):
        scenes.append(scene)

    assert len(scenes) >= 2
    assert scenes[0].metadata.index == 0
    assert scenes[1].metadata.index == 1
    assert scenes[1].frames._start > scenes[0].frames._start

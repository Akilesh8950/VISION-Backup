from vision_app.updater.model_updater import check_for_update, compare_sha


class FakeApi:
    def model_info(self, *_args, **_kwargs):
        return {"sha": "remote", "siblings": [{"rfilename": "model.gguf", "size": 123}]}


def test_compare_sha():
    assert compare_sha("a", "a") == "current"
    assert compare_sha("a", "b") == "different"
    assert compare_sha("", "b") == "newer_available"
    assert check_for_update("remote", api_factory=lambda _t: FakeApi()).status == "current"

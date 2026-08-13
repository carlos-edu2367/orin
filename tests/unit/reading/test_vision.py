import pytest

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.reading.selection import VisionModel
from agentos.reading.vision import VisionReader, VisionUnavailable


class _Transport:
    def __init__(self, items):
        self.items = items
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        return iter(self.items)


def _reader(transport):
    return VisionReader(lambda model: transport, model=VisionModel("ollama", "qwen2.5-vl"))


def test_transcription_joins_the_streamed_text():
    transport = _Transport([
        NormalizedStreamItem(StreamKind.TEXT, 1, text="Nota fiscal "),
        NormalizedStreamItem(StreamKind.TEXT, 2, text="nº 42"),
        NormalizedStreamItem(StreamKind.FINISH, 3),
    ])
    assert _reader(transport).transcribe([("QUJD", "image/png")]) == "Nota fiscal nº 42"


def test_the_request_carries_the_image_and_no_tools():
    transport = _Transport([NormalizedStreamItem(StreamKind.TEXT, 1, text="ok")])
    _reader(transport).transcribe([("QUJD", "image/png")], instruction="Liste os valores")
    request = transport.requests[0]
    assert request["tools"] == []
    content = request["messages"][0]["content"]
    assert content[0]["text"].startswith("Liste os valores")
    assert content[1] == {"type": "image", "media_type": "image/png", "data": "QUJD"}


def test_a_stream_error_raises_vision_unavailable():
    transport = _Transport([NormalizedStreamItem(StreamKind.ERROR, 1)])
    with pytest.raises(VisionUnavailable):
        _reader(transport).transcribe([("QUJD", "image/png")])


def test_an_empty_transcription_raises():
    transport = _Transport([NormalizedStreamItem(StreamKind.FINISH, 1)])
    with pytest.raises(VisionUnavailable):
        _reader(transport).transcribe([("QUJD", "image/png")])


def test_without_a_model_it_refuses_before_any_call():
    with pytest.raises(VisionUnavailable):
        VisionReader(lambda model: None, model=None).transcribe([("QUJD", "image/png")])

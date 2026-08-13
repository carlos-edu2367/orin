from agentos.agentic.session import pre_read_attachments


class _Toolset:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def view_file(self, path, question=""):
        self.calls.append(path)
        return self.outcome


def test_a_visual_attachment_is_read_and_appended():
    toolset = _Toolset({"content": "Leitura visual de 'uploads/nota.pdf':\n\nTotal 42"})
    history = [{"role": "user", "content": "quanto deu?\n\n[anexos enviados pela pessoa: uploads/nota.pdf (PDF, 12 KB)]"}]
    result = pre_read_attachments(history, [{"path": "uploads/nota.pdf", "kind": "pdf"}], toolset)
    assert toolset.calls == ["uploads/nota.pdf"]
    assert "Total 42" in result[-1]["content"]
    assert result[-1]["role"] == "user"


def test_a_vision_model_without_tools_receives_the_pre_read_image():
    toolset = _Toolset({
        "content": "A imagem 'uploads/foto.png' estÃ¡ anexada logo abaixo.",
        "images": [{"type": "image", "media_type": "image/jpeg", "data": "QUJD"}],
    })
    history = [{"role": "user", "content": "o que aparece?"}]

    result = pre_read_attachments(history, [{"path": "uploads/foto.png", "kind": "image"}], toolset)

    assert result[-1]["role"] == "user"
    assert isinstance(result[-1]["content"], list)
    assert result[-1]["content"][-1] == {"type": "image", "media_type": "image/jpeg", "data": "QUJD"}
    assert "o que aparece?" in result[-1]["content"][0]["text"]


def test_a_text_attachment_is_not_pre_read():
    toolset = _Toolset({"content": "irrelevante"})
    history = [{"role": "user", "content": "leia"}]
    result = pre_read_attachments(history, [{"path": "uploads/notas.md", "kind": "text"}], toolset)
    assert toolset.calls == []
    assert result == history


def test_a_failed_read_does_not_break_the_turn():
    class _Broken:
        def view_file(self, path, question=""):
            raise RuntimeError("boom")

    history = [{"role": "user", "content": "leia"}]
    result = pre_read_attachments(history, [{"path": "uploads/foto.png", "kind": "image"}], _Broken())
    assert result == history


def test_the_number_of_pre_read_files_is_bounded():
    toolset = _Toolset({"content": "ok"})
    attachments = [{"path": f"uploads/f{index}.png", "kind": "image"} for index in range(8)]
    pre_read_attachments([{"role": "user", "content": "leia"}], attachments, toolset, max_files=3)
    assert len(toolset.calls) == 3

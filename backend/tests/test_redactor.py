from paperhub.tracing.redactor import redact


def test_redacts_anthropic_key() -> None:
    payload = {"api_key": "sk-ant-api03-AAAABBBBCCCC"}
    assert redact(payload) == {"api_key": "<redacted:anthropic>"}


def test_redacts_openai_key() -> None:
    payload = {"key": "sk-proj-XYZ123"}
    assert redact(payload) == {"key": "<redacted:openai>"}


def test_redacts_google_key() -> None:
    payload = {"k": "AIzaSyAbCdEfGhIjKlMnOpQrStUv"}
    assert redact(payload) == {"k": "<redacted:google>"}


def test_redacts_home_path(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/alice")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\alice")
    payload = {"path": "/home/alice/secrets.txt"}
    assert redact(payload) == {"path": "$HOME/secrets.txt"}


def test_redacts_in_nested_structures() -> None:
    payload = {"args": ["sk-ant-api03-XX", {"x": "AIzaABCDEFGHIJ"}]}
    out = redact(payload)
    assert out == {"args": ["<redacted:anthropic>", {"x": "<redacted:google>"}]}


def test_leaves_safe_values_alone() -> None:
    payload = {"intent": "paper_qa", "confidence": 0.91, "n": 42}
    assert redact(payload) == payload

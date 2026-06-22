from pathlib import Path


def _template_text() -> str:
    template = Path(__file__).resolve().parents[1] / "scripts" / "com.tokgain.daily.plist.template"
    return template.read_text(encoding="utf-8")


def test_launchd_runs_all_collectors():
    text = _template_text()

    assert "<string>--tool</string>" in text
    assert "<string>all</string>" in text
    assert "<string>auto</string>" not in text


def test_launchd_allows_partial_adapter_errors_after_recording_them():
    text = _template_text()

    assert "<string>--allow-errors</string>" in text


def test_launchd_has_tool_path_for_adapters():
    text = _template_text()

    assert "<key>EnvironmentVariables</key>" in text
    assert "__HOME__/.local/bin" in text
    assert "/opt/homebrew/bin" in text
    assert "/usr/local/bin" in text

from browser_agent import web_fetch


def test_web_fetch_success():
    """Test simple HTTP fetch works."""
    result = web_fetch("https://httpbin.org/get", extract_text=True)
    assert result["success"] is True
    assert result["output"]  # non-empty
    assert result["url"] == "https://httpbin.org/get"


def test_web_fetch_bad_url():
    """Test graceful failure on bad URL."""
    result = web_fetch("https://this-domain-does-not-exist-12345.com")
    assert result["success"] is False
    assert result["error"]  # non-empty error message


def test_web_fetch_html_stripping():
    """Test that HTML tags are stripped when extract_text=True."""
    result = web_fetch("https://httpbin.org/html", extract_text=True)
    assert result["success"] is True
    assert "<" not in result["output"][:200]  # HTML tags stripped

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"
ANDROID_DASHBOARD = (
    ROOT / "android" / "app" / "src" / "main" / "assets" / "dashboard" / "index.html"
)


def test_android_dashboard_matches_live_root_dashboard():
    root_html = DASHBOARD.read_text(encoding="utf-8")
    android_html = ANDROID_DASHBOARD.read_text(encoding="utf-8")

    assert android_html == root_html, "Android must embed the current live dashboard."


def test_live_dashboard_uses_authenticated_server_data_not_offline_demo_fixtures():
    html = ANDROID_DASHBOARD.read_text(encoding="utf-8")

    for endpoint in (
        "/api/market",
        "/api/dashboard",
        "/api/commands",
        "/api/paper-control",
        "/api/emergency-stop",
    ):
        assert endpoint in html, f"live dashboard endpoint is missing: {endpoint}"

    assert "SYNTHETIC · NOT BINANCE" not in html
    assert "DEMO · NOT RUNNING" not in html
    assert "account:{" not in html

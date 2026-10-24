import json
import time

import pytest
from fastapi.testclient import TestClient
from real_estate_finder.api import create_app
from real_estate_finder.cli import app as cli
from real_estate_finder.models import OpportunitySeed
from real_estate_finder.service import Service
from typer.testing import CliRunner


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Service(tmp_path), testing=True)) as client:
        yield client


def headers(client):
    return {"X-Session-Token": client.get("/api/session").json()["token"]}


def wait_for_run(client, run_id):
    for _ in range(200):
        result = client.get(f"/api/runs/{run_id}").json()
        if result["run"]["status"] not in {"running", "queued"}:
            return result
        time.sleep(0.03)
    pytest.fail("Offline demo did not finish within the expected bounded interval")


def test_actual_ui_assets_and_health_are_served(client):
    assert client.get("/").status_code == 200
    assert "text/css" in client.get("/static/app.css").headers["content-type"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/health/ready").json()["ready"]
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Real Estate Finder"


def test_mutations_need_session_and_same_origin(client):
    assert client.post("/api/demo").status_code == 403
    assert client.post("/api/demo", headers=headers(client) | {"Origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/demo", headers=headers(client)).status_code == 200


def test_api_demo_run_review_tracking_export_roundtrip(client):
    token = headers(client)
    demo = client.post("/api/demo?mode=commercial_lease", headers=token).json()
    search, run = demo["search"], demo["run"]
    result = wait_for_run(client, run["id"])
    assert result["run"]["status"] == "completed", result
    detail = client.get(f"/api/searches/{search['id']}").json()
    assert search["mode"] == "commercial_lease"
    assert detail["counts"]["drafted"] >= 1
    assert len(detail["properties"]) >= 3
    assert detail["properties"] == detail["opportunities"]
    property_row = next(row for row in detail["properties"] if row["drafts"] and not row["qa_issues"])
    approved = client.patch(f"/api/properties/{property_row['id']}", json={"status": "approved"}, headers=token)
    assert approved.status_code == 200, approved.text
    assert not approved.json()["ready_to_contact"]
    tracked = client.patch(f"/api/properties/{property_row['id']}", json={"workflow_status": "shortlisted", "next_follow_up": "2027-01-01"}, headers=token)
    assert tracked.status_code == 200
    assert tracked.json()["workflow_status"] == "shortlisted"
    exported = client.get(f"/api/searches/{search['id']}/export?format=csv")
    assert exported.status_code == 200
    assert "listing_url" in exported.text
    assert "price_basis" in exported.text
    assert client.get(f"/api/searches/{search['id']}/export?scope=outreach").status_code == 400
    assert client.get(f"/api/searches/{search['id']}/export?scope=shortlist").status_code == 200


def test_document_and_property_import_via_real_multipart(client):
    token = headers(client)
    search = client.post("/api/searches", json={"name": "Import", "objective": "Find workshop space", "mode": "commercial_lease"}, headers=token).json()
    root = f"/api/searches/{search['id']}"
    response = client.post(root + "/documents", files={"file": ("needs.md", b"I need loading access and three-phase power.", "text/plain")}, headers=token)
    assert response.status_code == 200
    doc = response.json()
    response = client.post(root + "/import", files={"file": ("properties.csv", b"mode,title,listing_url,price_amount,price_basis,currency\ncommercial_lease,Example Workshop,https://fixture.example/property/1,18,sqft_year,USD\n", "text/csv")}, headers=token)
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1
    assert client.delete(root + "/documents/" + doc["id"], headers=token).status_code == 200


def test_validation_errors_do_not_echo_private_inputs(client):
    response = client.post("/api/searches", json={"name": "", "client": {"brief_text": "private financing and identity details"}}, headers=headers(client))
    assert response.status_code == 422
    assert "private financing" not in response.text
    assert response.json()["error"]["code"] == "INPUT-INVALID"


@pytest.mark.parametrize("mode", ["residential_rent", "residential_buy", "commercial_lease", "commercial_buy"])
def test_cli_demo_modes_are_separate_and_persisted(tmp_path, mode):
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "demo", "--mode", mode, "--json"])
    assert result.exit_code == 0, result.output
    search = json.loads(result.stdout)["search"]
    assert search["mode"] == mode
    assert search["demo"] is True
    assert Service(tmp_path).store.get("searches", search["id"])["mode"] == mode


def test_cli_json_is_clean_and_shares_persistence(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["--data-dir", str(tmp_path), "demo", "--run", "--json"])
    assert result.exit_code == 0, result.output
    run = json.loads(result.stdout)["run"]
    assert run["status"] == "completed"
    assert Service(tmp_path).search_detail(run["search_id"])["counts"]["drafted"] >= 1
    listed = runner.invoke(cli, ["--data-dir", str(tmp_path), "properties", run["search_id"], "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["properties"]
    path = tmp_path / "research.csv"
    exported = runner.invoke(cli, ["--data-dir", str(tmp_path), "export", run["search_id"], "--output", str(path), "--json"])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout)["bytes"] > 1000
    assert path.exists()


def test_cli_errors_have_stable_exit_and_json(tmp_path):
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "show", "missing", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "NOT-FOUND"


def test_cli_create_config_routes_brief_and_activity_use_shared_services(tmp_path):
    runner = CliRunner()
    config = tmp_path / "search.json"
    config.write_text(json.dumps({
        "name": "Workshop search", "objective": "Find a useful industrial space",
        "client": {"name": "Taylor Example", "organization": "Fictional Example Studio"},
        "settings": {"provider": "codex", "routes": {"research": {"provider": "claude", "model": "research-model"}}},
    }))
    brief = tmp_path / "needs.md"
    brief.write_text("I need loading access and 2,000 square feet for light fabrication.")
    result = runner.invoke(cli, [
        "--data-dir", str(tmp_path / "data"), "search", "create", "--config", str(config),
        "--brief", str(brief), "--mode", "commercial_buy", "--property-type", "industrial", "--location", "Dayton, OH",
        "--price-max", "500000", "--monthly-budget", "5000", "--cash-available", "150000",
        "--financing", "mortgage", "--down-payment", "25", "--interest-rate", "6.5", "--loan-years", "20",
        "--outreach-provider", "codex", "--outreach-model", "draft-model", "--target", "30", "--json",
    ])
    assert result.exit_code == 0, result.output
    search = json.loads(result.stdout)
    assert search["settings"]["target_results"] == 30
    assert search["settings"]["routes"]["research"] == {"provider": "claude", "model": "research-model"}
    assert search["settings"]["routes"]["outreach"] == {"provider": "codex", "model": "draft-model"}
    assert search["mode"] == "commercial_buy"
    assert search["preferences"]["purchase_price_max"] == 500000
    assert search["cost_assumptions"]["down_payment_percent"] == 25
    assert search["cost_assumptions"]["interest_rate_percent"] == 6.5
    assert search["cost_assumptions"]["loan_years"] == 20
    service = Service(tmp_path / "data")
    assert len(service.store.documents(search["id"])) == 1
    property_row, _ = service.upsert_seed(search["id"], OpportunitySeed(mode="commercial_buy", title="Example Workshop", listing_url="https://fixture.example/property/123"))
    tracked = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "activity", property_row["id"], "--status", "tour_scheduled", "--follow-up", "2027-01-01", "--json"])
    assert tracked.exit_code == 0, tracked.output
    assert json.loads(tracked.stdout)["workflow_status"] == "tour_scheduled"
    assert service.store.get("opportunities", property_row["id"])["next_follow_up"] == "2027-01-01"


def test_cli_nonobject_configuration_and_invalid_routes_fail_cleanly(tmp_path):
    runner = CliRunner()
    config = tmp_path / "bad.json"
    config.write_text("[]")
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "search", "create", "--config", str(config), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "INPUT-INVALID"
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "search", "create", "--research-provider", "unsupported", "--json"])
    assert result.exit_code == 2
    assert Service(tmp_path / "data").store.list("searches") == []


def test_cli_schema_and_export_overwrite_guard(tmp_path):
    runner = CliRunner()
    schema = runner.invoke(cli, ["schema", "search", "--json"])
    assert schema.exit_code == 0
    assert "cost_assumptions" in json.loads(schema.stdout)["properties"]
    assert "client" in json.loads(schema.stdout)["properties"]
    property_schema = runner.invoke(cli, ["schema", "property", "--json"])
    assert property_schema.exit_code == 0
    assert "price_basis" in json.loads(property_schema.stdout)["properties"]
    existing = tmp_path / "keep.csv"
    existing.write_text("Keep this data")
    result = runner.invoke(cli, ["--data-dir", str(tmp_path / "data"), "export", "missing", "--output", str(existing), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "FILE-EXISTS"
    assert existing.read_text() == "Keep this data"

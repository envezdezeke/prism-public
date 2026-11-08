"""Upload-run guards: bad zips, zip-slip, missing layout, bad set name, unknown run id."""
import io
import zipfile

from fastapi.testclient import TestClient

from prism.api.app import create_app


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _post(body: bytes, set_name: str = "hidden"):
    c = TestClient(create_app())
    return c.post(f"/api/upload-run?set={set_name}", content=body, headers={"Content-Type": "application/zip"})


def test_not_a_zip_is_400():
    assert _post(b"hello").status_code == 400


def test_empty_is_400():
    assert _post(b"").status_code == 400


def test_zip_slip_is_refused():
    r = _post(_zip({"../../evil.txt": b"x"}))
    assert r.status_code == 400
    assert "outside" in r.json()["detail"]


def test_missing_shared_folder_is_422():
    r = _post(_zip({"drop/sim/arrivals.jsonl": b""}))
    assert r.status_code == 422
    assert "shared_folder" in r.json()["detail"]


def test_missing_arrivals_is_422():
    r = _post(_zip({"drop/shared_folder/A/application.pdf": b"%PDF"}))
    assert r.status_code == 422
    assert "arrivals" in r.json()["detail"]


def test_bad_set_name_is_422():
    assert _post(_zip({"a": b"x"}), set_name="../x").status_code == 422


def test_unknown_run_is_404():
    c = TestClient(create_app())
    assert c.get("/api/upload-run/../../etc/submission").status_code == 404
    assert c.get("/api/upload-run/20990101-000000-abcdef/submission").status_code == 404

"""Uvicorn entry point for the control plane."""
from api.main import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=False)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ContainerRequest(BaseModel):
    container_name: str
    container_image: str
    port: str

@app.post("/api/containers")
def run_container(data: ContainerRequest):
    cmd = [
        "docker", "run", "-d",
        "--name", data.container_name,
        "-p", f"{data.port}:{data.port}",
        data.container_image
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return {"status": "success", "container_id": result.stdout.strip()}
    else:
        return {"status": "error", "message": result.stderr.strip()}

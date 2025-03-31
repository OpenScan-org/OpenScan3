import uvicorn
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware

from routers import cameras, motors, projects, cloud, gpio, paths, openscan, lights, device

# Import and initialize hardware manager
from app.controllers import device as device_controller
device_controller.initialize()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router)
app.include_router(motors.router)
app.include_router(lights.router)
app.include_router(projects.router)
app.include_router(gpio.router)
app.include_router(openscan.router)

app.include_router(device.router)

app.include_router(cloud.router)
app.include_router(paths.router)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
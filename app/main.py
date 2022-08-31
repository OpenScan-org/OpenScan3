from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware

from .routers import cameras, motors, projects, cloud, io, paths, scanner

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
app.include_router(projects.router)
app.include_router(io.router)
app.include_router(scanner.router)

app.include_router(cloud.router)
app.include_router(paths.router)


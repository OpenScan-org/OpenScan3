from fastapi import FastAPI

from .routers import cameras, motors, projects, scanner, cloud, io, paths

app = FastAPI()

app.include_router(cameras.router)
app.include_router(motors.router)
app.include_router(projects.router)
app.include_router(scanner.router)
app.include_router(io.router)

app.include_router(cloud.router)
app.include_router(paths.router)

@app.get("/")
async def root():
    return {"message": "Hello World"}

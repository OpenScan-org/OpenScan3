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


#@app.get("/")
#async def get_scanner():
#    return {"status": "ok"}


#@app.post("/move_to")
#async def move_to_point(point: PolarPoint3D):
#    scanner.move_to_point(point)


#@app.post("/scan")
#async def scan(
#    project_name: str = Body(embed=True),
#    camera_id: int = Body(embed=True),
#    method: PathMethod = Body(embed=True),
#    points: int = Body(embed=True),
#):
#    project = projects.new_project(f"{project_name}")
#    camera = cameras.get_camera(camera_id)
#    path = paths.get_path(method, points)
#    scanner.scan(project, camera, path)


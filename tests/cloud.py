from app.controllers import cloud
from app.controllers import projects

project_name = "openscan_test4"

project = projects.get_project(project_name)

cloud.upload_project(project.name)

# zip = projects.compress_project_photos(project)
# zip_size = zip.tell()

# print(zip, zip_size)

# with open(f"{project_name}.zip", "wb") as f:
#     zip.seek(0)
#     f.write(zip.read())

# counter = 0
# for chunk in projects.split_file(zip):
#     with open(f"split_{counter}", "wb") as f:
#         f.write(chunk.read())
#     counter += 1
#     print(counter)

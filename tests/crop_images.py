import os
from PIL import Image

project_name = "openscan_test4"
photos = [f for f in os.listdir(f"projects/{project_name}") if f.endswith(".jpg")]

os.makedirs(f"projects/{project_name}_reduced", exist_ok=True)

for photo in photos:
    with Image.open(f"projects/{project_name}/{photo}") as img:
        img.load()
        reduced = img.crop((1800, 500, 4000, 3200))
        print(reduced.size)
        reduced.save(f"projects/{project_name}_reduced/{photo}")

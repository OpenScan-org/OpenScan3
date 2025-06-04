import requests
import time

# lights on
# for pin in (17, 27):
#     requests.put(f"http://127.0.0.1:8000/io/{pin}", params={"status": True})

requests.delete("http://127.0.0.1:8000/projects/openscan_test4")
requests.post("http://127.0.0.1:8000/projects/openscan_test4")

for i in (10, -10, -10):
    requests.post("http://127.0.0.1:8000/motors/rotor/move", params={"degrees": i})
    for _ in range(0, 360, 20):
        requests.post("http://127.0.0.1:8000/motors/tt/move", params={"degrees": 20})
        time.sleep(5)
        requests.put(
            "http://127.0.0.1:8000/projects/openscan_test4/photo",
            params={"camera_id": 0},
        )

requests.post("http://127.0.0.1:8000/motors/rotor/move", params={"degrees": 10})


#lights off
# for pin in (17, 27):
#     requests.put(f"http://127.0.0.1:8000/io/{pin}", params={"status": False})

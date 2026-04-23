# Rover Swarm Laptop UI (Flask)

This folder contains a **laptop-side** Flask server for controlling multiple rovers from one dashboard.

## Features

- Select active rover from configured rover list
- Static camera placeholder panel (per current requirements)
- Health summary cards (status, max temp, CPU load, memory, disk)
- Python code editor to execute snippets on selected rover via `:8002/execute`
- Execution output viewer (stdout / stderr / traceback / error)
- SSH launch button that opens Windows Command Prompt with:
  - `ssh rover@<selected-rover-host>`
  - Password is entered manually when prompted

## Folder layout

- `app.py` - Flask app entrypoint
- `config/rovers.yaml` - manual rover host/IP list
- `services/rover_client.py` - API calls to rover FastAPI service
- `services/ssh_launcher.py` - Windows SSH terminal launch helper
- `templates/index.html` - dashboard template
- `static/css/styles.css` - dashboard styling
- `static/js/app.js` - UI interaction logic

## Setup

1. Create and activate a Python virtual environment.
2. Install dependencies from `requirements.txt`.
3. Edit `config/rovers.yaml` with your rover hosts/IPs.
4. Run `python app.py` from this `UI` folder.
5. Open the printed local URL in a browser.

## Rover config example

```yaml
rovers:
  - name: rover0
    host: 192.168.137.101
    control_port: 8002
    camera_port: 8001
    ssh_username: rover
```

## Notes

- The UI talks to each rover control server at `http://<host>:8002`.
- The code execution endpoint allows arbitrary Python execution on rover by design.
- For production/safety, add local auth and network restrictions before broader use.

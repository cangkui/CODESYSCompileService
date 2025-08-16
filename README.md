# Deployment Tutorial

[👉中文版本](README_zh.md)

## Environment Configuration

### CODESYS Configuration
1. The recommended operating system is Windows 10 or above. The tested CODESYS version is `3.5.20.40 (V3.5) SP20 Patch 4`.
2. In `HTTP_SERVER.py`, modify the path of `CODESYS.exe` to the path of `CODESYS.exe`installed in your current system.
3. Extract template project. The template project we provided contains 3 additional libraries: OSCAT BASIC, OSCAT NETWORK and OSCAT BUILDING. If you rely on additional libraries, please open the extracted `.project` in CODESYS and download them through the Library Manager yourself.  

> In CODESYS, File->Project Archive->Extract Archive, open the `.projectarchive` file in `template`
> Select `Extract into the same folder where the archive is located`, and click `Extract`
> Template project will be generated in `template`, then save project and exit.

After Extracting, the template project will like this:

![Template Project](images/codesys_template.png)

### Python Environment Configuration
Create and activate a virtual environment using the venv module on Windows.
```
python -m venv ./venv
.\venv\Scripts\activate.bat
pip install -r requirements.txt

# or use uv
uv venv ./venv
.\venv\Scripts\activate.bat
uv pip install -r requirements.txt
```

## Running

After configuration, you can directly run the server and client through `start.bat`. The `start.bat` is a startup script that mainly does the following:

### Server Side
Run the debug server in a Command Prompt terminal started with administrative privileges.
```
python debug_server.py
```
At this time, the program will output the deployed service address, including the IP address and port number within the local area network, and will keep occupying the current terminal.

If the program exits directly without output or does not respond after running, please check the log file `codesys_api_server.log` in the same directory to check the cause of the error.

The deployed IP address and port number are defaulted to the current network card address + 9000, which can be modified and located in the `HTTP_SERVER.py` file.

### Client Side for Initializing
After the server runs successfully, we need to start a client example client to call some http apis for initialization, completing the creation of the session and the test project.

If needed, you can integrate the initialization function into your own project.

The example client will obtain the request IP address and port from the debug server log.
```
python example_client.py
```
When running the example client, the initialization operation will launch the CODESYS GUI interface which prompts you to select a profile to start, and you need to manually click the "Continue" button to proceed with subsequent operations.

Wait for the initialization to complete. When the example client outputs information similar to the following, the initialization is successfully completed.

```
2025-05-27 21:47:12,002 - codesys_api_client - INFO - Project created successfully
2025-05-27 21:47:12,002 - codesys_api_client - INFO - Actual project path: E:\Openness\CODESYSCompileAPI\projects\CODESYS_Test_Project.project
2025-05-27 21:47:12,002 - codesys_api_client - INFO - Project file verified to exist on disk
Press enter to start example workflow or Ctrl+C to exit...
```

The last lines of `codesys_api_server.log` look something like this:

```
2025-05-27 21:47:11,796 - codesys_api_server - INFO - Result file found after 3.22 seconds (33 checks)
2025-05-27 21:47:11,997 - codesys_api_server - INFO - Script execution successful
2025-05-27 21:47:11,997 - codesys_api_server - INFO - Script execution result: {'executed_by': 'CODESYS PersistentSession', 'project': {'dirty': False, 'name': 'CODESYS_Test_Project.project', 'path': 'E:\\Openness\\CODESYSCompileAPI\\projects\\CODESYS_Test_Project.project'}, 'request_id': 'f78e2e09-613f-4f8d-87a8-caac3ecf4913', 'execution_time': 1748353631.7139053, 'success': True}
2025-05-27 21:47:11,997 - codesys_api_server - INFO - Project creation successful
```

# Acknowledgments

This project was completed with reference to the following project:

[codesys-api by claude](https://github.com/johannesPettersson80/codesys-api)

It is worth noting that the author of this project used Claude for coding. We have tested it on CODESYS, fixed many functional errors, and improved the core working mode to ensure support for at least 4-5 concurrent accesses.

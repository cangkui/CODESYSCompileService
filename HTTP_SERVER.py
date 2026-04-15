#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CODESYS API HTTP Server

This script implements a HTTP server for the CODESYS API wrapper.
It provides RESTful endpoints to interact with CODESYS through
a persistent session.

Note: This script requires Python 3.x.
Only the PERSISTENT_SESSION.py script maintains compatibility with
CODESYS IronPython environment.
"""

import sys
import os
import json
import time
import subprocess
import threading
import tempfile
import uuid
import logging
import traceback
import re
import socket
from pathlib import Path

# Python 3 compatibility imports
try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import urllib.parse as urlparse
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    import urlparse

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='codesys_api_server.log'
)
logger = logging.getLogger('codesys_api_server')

def get_local_ipv4():
    """Get IPv4 address of local non-loopback NIC"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a public network IP (real reachability is not required, just trigger interface selection)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
    return local_ip

# Constants
SERVER_HOST = get_local_ipv4()
SERVER_PORT = 9000
CODESYS_PATH = r"D:\Program Files\CODESYS 3.5.20.40\CODESYS\Common\CODESYS.exe"  # Path provided by user
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PERSISTENT_SCRIPT = os.path.join(SCRIPT_DIR, "PERSISTENT_SESSION.py")
API_KEY_FILE = os.path.join(SCRIPT_DIR, "api_keys.json")
REQUEST_DIR = os.path.join(SCRIPT_DIR, "requests")
RESULT_DIR = os.path.join(SCRIPT_DIR, "results")
TEMP_DIR = os.path.join(SCRIPT_DIR, "temp")
TERMINATION_SIGNAL_FILE = os.path.join(SCRIPT_DIR, "terminate.signal")
STATUS_FILE = os.path.join(SCRIPT_DIR, "session_status.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "session.log")

# Ensure directories exist with proper permissions
def ensure_directory(path):
    """Ensure directory exists with proper permissions."""
    if not os.path.exists(path):
        try:
            os.makedirs(path)
            logger.info("Created directory: %s", path)
        except Exception as e:
            logger.error("Error creating directory %s: %s", path, str(e))
            raise
    
    # Check if directory is writable
    if not os.access(path, os.W_OK):
        logger.error("Directory %s is not writable", path)
        raise PermissionError("Directory {} is not writable".format(path))
    
    return path

# Create necessary directories
ensure_directory(REQUEST_DIR)
ensure_directory(RESULT_DIR)
# TEMP_DIR = tempfile.gettempdir()
ensure_directory(TEMP_DIR)

class CodesysProcessManager:
    """Manages the CODESYS process."""
    
    def __init__(self, codesys_path, script_path):
        self.codesys_path = codesys_path
        self.script_path = script_path
        self.process = None
        self.running = False
        self.lock = threading.Lock()
        
    def start(self):
        """Start the CODESYS process.
        
        Returns:
            bool: True if process started successfully, False otherwise
        """
        with self.lock:
            try:
                # Check if CODESYS is already running
                if self.is_running():
                    logger.info("CODESYS process already running")
                    return True
                
                # Verify CODESYS executable exists
                if not os.path.exists(self.codesys_path):
                    logger.error("CODESYS executable not found at path: %s", self.codesys_path)
                    return False
                
                # Verify script exists
                if not os.path.exists(self.script_path):
                    logger.error("CODESYS script not found at path: %s", self.script_path)
                    return False
                    
                logger.info("Starting CODESYS process with script: %s", self.script_path)
                
                # Clear any existing termination signal
                if os.path.exists(TERMINATION_SIGNAL_FILE):
                    os.remove(TERMINATION_SIGNAL_FILE)
                
                # Delete any existing status file to ensure we don't detect an old one
                if os.path.exists(STATUS_FILE):
                    try:
                        os.remove(STATUS_FILE)
                        logger.info("Removed existing status file")
                    except Exception as e:
                        logger.warning("Could not remove existing status file: %s", str(e))
                
                # Create logs directory if needed
                log_dir = os.path.dirname(LOG_FILE)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir)
                
                # Start CODESYS with script and proper Python path
                try:
                    # Get ScriptLib directory path for Python imports
                    script_lib_path = os.path.join(SCRIPT_DIR, "ScriptLib")
                    
                    # Set up environment with PYTHONPATH
                    env = os.environ.copy()
                    if "PYTHONPATH" in env:
                        env["PYTHONPATH"] = script_lib_path + os.pathsep + env["PYTHONPATH"]
                    else:
                        env["PYTHONPATH"] = script_lib_path
                    
                    logger.info("Starting CODESYS with PYTHONPATH: %s", env["PYTHONPATH"])
                    # Use the exact command format that worked in pure_test.bat
                    # Construct full command with proper quoting
                    command = f"\"{self.codesys_path}\" --runscript=\"{self.script_path}\""
                    
                    logger.info("Starting CODESYS with command: %s", command)
                    self.process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                        shell=True  # Use shell to handle the command as a string
                    )
                except subprocess.SubprocessError as se:
                    logger.error("SubprocessError starting CODESYS: %s", str(se))
                    return False
                except FileNotFoundError:
                    logger.error("CODESYS executable not found. Check the path: %s", self.codesys_path)
                    return False
                
                # Wait for process to be visibly running
                logger.info("Waiting for CODESYS process to start...")
                max_wait = 30  # seconds
                wait_interval = 1
                total_waited = 0
                
                while total_waited < max_wait:
                    time.sleep(wait_interval)
                    total_waited += wait_interval
                    
                    # Check if process is still running
                    if not self.is_running():
                        try:
                            stdout, stderr = self.process.communicate(timeout=1)
                            stderr_text = stderr.decode('utf-8', errors='replace') if stderr else "No error output"
                            stdout_text = stdout.decode('utf-8', errors='replace') if stdout else "No standard output"
                            logger.error("CODESYS process failed to start:\nStderr: %s\nStdout: %s", stderr_text, stdout_text)
                        except Exception as e:
                            logger.error("Error communicating with failed process: %s", str(e))
                        return False
                    
                    # Check if status file exists, indicating the script has started
                    if os.path.exists(STATUS_FILE):
                        logger.info("Status file detected after %.1f seconds", total_waited)
                        break
                    
                    logger.debug("Waiting for CODESYS initialization... (%.1f seconds elapsed)", total_waited)
                
                # Now wait for CODESYS to fully initialize
                # Even if status file exists, we want to wait a bit longer for full initialization
                logger.info("CODESYS process has started. Waiting for full initialization...")
                
                # Additional wait to ensure CODESYS is fully initialized
                additional_wait = 10  # seconds
                logger.info("Waiting additional %d seconds for full initialization...", additional_wait)
                time.sleep(additional_wait)
                
                # Final check if the process is running
                if not self.is_running():
                    logger.error("CODESYS process failed to initialize properly")
                    return False
                    
                # Create a status file if it doesn't exist
                # This is a workaround for when CODESYS starts but doesn't create the status file
                if not os.path.exists(STATUS_FILE):
                    logger.warning("CODESYS started but didn't create status file. Creating a default one.")
                    try:
                        with open(STATUS_FILE, 'w') as f:
                            f.write(json.dumps({
                                "state": "initialized",
                                "timestamp": time.time()
                            }))
                    except Exception as e:
                        logger.error("Error creating default status file: %s", str(e))
                    
                self.running = True
                logger.info("CODESYS process started and fully initialized")
                return True
            except Exception as e:
                logger.error("Error starting CODESYS process: %s", str(e))
                return False
                
    def stop(self):
        """Stop the CODESYS process.
        
        Returns:
            bool: True if process stopped successfully or was not running, False otherwise
        """
        with self.lock:
            if not self.is_running():
                logger.info("CODESYS process not running")
                return True
                
            try:
                logger.info("Stopping CODESYS process")
                
                # Signal termination through file
                try:
                    with open(TERMINATION_SIGNAL_FILE, 'w') as f:
                        f.write("TERMINATE")
                    logger.debug("Created termination signal file")
                except Exception as e:
                    logger.warning("Could not create termination signal file: %s", str(e))
                    # Continue with process termination anyway
                    
                # Wait for process to terminate gracefully
                max_wait = 10  # seconds
                wait_interval = 0.5
                waited = 0
                
                while waited < max_wait:
                    if not self.is_running():
                        break
                    time.sleep(wait_interval)
                    waited += wait_interval
                
                # Force termination if still running
                if self.is_running():
                    logger.info("Process still running after %s seconds, sending TERMINATE signal", waited)
                    try:
                        self.process.terminate()
                    except Exception as e:
                        logger.warning("Error terminating process: %s", str(e))
                        
                    # Wait again for termination
                    time.sleep(2)
                    
                    # Kill if still running
                    if self.is_running():
                        logger.warning("Process still running after TERMINATE signal, sending KILL signal")
                        try:
                            self.process.kill()
                        except Exception as e:
                            logger.error("Error killing process: %s", str(e))
                            return False
                
                # Clean up
                self.process = None
                self.running = False
                
                # Remove termination signal file if it exists
                if os.path.exists(TERMINATION_SIGNAL_FILE):
                    try:
                        os.remove(TERMINATION_SIGNAL_FILE)
                    except Exception as e:
                        logger.warning("Could not remove termination signal file: %s", str(e))
                
                logger.info("CODESYS process stopped successfully")
                return True
            except Exception as e:
                logger.error("Error stopping CODESYS process: %s", str(e))
                return False
                
    def is_running(self):
        """Check if CODESYS process is running."""
        if self.process is None:
            return False
            
        return self.process.poll() is None
        
    def get_status(self):
        """Get CODESYS session status."""
        try:
            if not os.path.exists(STATUS_FILE):
                return {"state": "unknown", "timestamp": time.time()}
                
            with open(STATUS_FILE, 'r') as f:
                return json.loads(f.read())
        except Exception as e:
            logger.error("Error getting CODESYS status: %s", str(e))
            return {"state": "error", "timestamp": time.time(), "error": str(e)}


class ScriptExecutor:
    """Executes scripts through the CODESYS persistent session."""
    
    def __init__(self, request_dir, result_dir):
        self.request_dir = request_dir
        self.result_dir = result_dir
        
    def execute_script(self, script_content, timeout=60):
        """Execute a script and return the result.
        
        Args:
            script_content (str): The script content to execute
            timeout (int): Timeout in seconds (default: 60 seconds)
            
        Returns:
            dict: The result of the script execution
        """
        request_id = str(uuid.uuid4())
        script_path = None
        result_path = None
        request_path = None
        
        try:
            # Log script execution start with more info
            logger.info("Executing script (request ID: %s, timeout: %s seconds)", request_id, timeout)
            script_preview = script_content[:500].replace('\n', ' ')
            logger.info("Script preview: %s...", script_preview)
            
            # Create dedicated directory for this request to avoid path issues
            request_dir = os.path.join(TEMP_DIR, f"codesys_req_{request_id}")
            if not os.path.exists(request_dir):
                os.makedirs(request_dir)
                logger.debug("Created request directory: %s", request_dir)
            
            # Create temporary script file with UTF-8 encoding explicitly
            script_path = os.path.join(request_dir, "script.py")
            try:
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write(script_content)
                logger.info("Created script file: %s", script_path)
                logger.debug("Script file size: %d bytes", os.path.getsize(script_path))
            except Exception as e:
                logger.error("Failed to write script file: %s", str(e))
                return {"success": False, "error": "Failed to write script file: " + str(e)}
                
            # Create result file path in same dedicated directory
            result_path = os.path.join(request_dir, "result.json")
            
            # Create request file with backslash-escaped paths for Windows
            request_path = os.path.join(self.request_dir, "{0}.request".format(request_id))
            tmp_req_path = os.path.join(self.request_dir, "{0}.tmp".format(request_id))
            try:
                with open(tmp_req_path, 'w', encoding='utf-8') as f:
                    # Use double backslashes for Windows path escaping
                    request_data = {
                        "script_path": script_path.replace("\\", "\\\\"),
                        "result_path": result_path.replace("\\", "\\\\"),
                        "timestamp": time.time(),
                        "request_id": request_id
                    }
                    f.write(json.dumps(request_data))
                    # Forced write to disk to keep consistency
                    f.flush()
                    os.fsync(f.fileno())
                
                # atomic operation
                os.rename(tmp_req_path, request_path)

                logger.info("Created request file: %s", request_path)
                logger.debug("Request data: %s", json.dumps(request_data))
            except Exception as e:
                logger.error("Failed to write request file: %s", str(e))
                return {"success": False, "error": "Failed to write request file: " + str(e)}
                
            # Wait for result with progressive polling
            logger.info("Waiting for script execution to complete (max: %s seconds)...", timeout)
            start_time = time.time()
            check_count = 0
            last_log_time = start_time
            
            # Use progressive polling intervals - start fast, then get slower
            poll_interval = 0.1  # Start with checking every 100ms
            
            while time.time() - start_time < timeout:
                check_count += 1
                
                # Check for result file
                if os.path.exists(result_path):
                    # Log result found
                    elapsed = time.time() - start_time
                    logger.info("Result file found after %.2f seconds (%d checks)", elapsed, check_count)
                    
                    # Read result with retry for potentially incomplete files
                    retry_count = 0
                    max_retries = 20
                    file_size = os.path.getsize(result_path)
                    
                    while retry_count < max_retries:
                        try:
                            # Wait a moment for the file to be fully written
                            time.sleep(0.2)
                            
                            # Check if file size changed
                            new_size = os.path.getsize(result_path)
                            if new_size != file_size:
                                logger.debug("Result file size changed from %d to %d bytes, waiting...", 
                                            file_size, new_size)
                                file_size = new_size
                                retry_count += 1
                                continue
                            
                            with open(result_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                try:
                                    result = json.loads(content)
                                    
                                    # Log result summary
                                    if result.get('success', False):
                                        logger.info("Script execution successful")
                                    else:
                                        error = result.get('error', 'Unknown error')
                                        logger.warning("Script execution failed: %s", error)
                                    
                                    # Cleanup files
                                    # self._cleanup_files(script_path, result_path, request_path, request_dir)
                                    
                                    return result
                                except json.JSONDecodeError as je:
                                    logger.warning("Invalid JSON in result file (attempt %d/%d): %s", 
                                                 retry_count+1, max_retries, str(je))
                                    logger.debug("Result file content: %s", content)
                                    
                                    # Try again after a short delay
                                    retry_count += 1
                                    time.sleep(0.5)
                        except Exception as e:
                            logger.warning("Error reading result file (attempt %d/%d): %s", 
                                         retry_count+1, max_retries, str(e))
                            retry_count += 1
                            time.sleep(0.5)
                    
                    # If we get here, we've exhausted retries
                    logger.error("Failed to read valid result after %d retries", max_retries)
                    return {"success": False, "error": f"Failed to read valid result after {max_retries} retries"}
                
                # Periodic status logging
                current_time = time.time()
                if current_time - last_log_time > 10:  # Log every 10 seconds
                    elapsed = current_time - start_time
                    logger.info("Still waiting for script execution (elapsed: %.2f seconds, checks: %d)", 
                               elapsed, check_count)
                    
                    # Log if script and request files still exist
                    if os.path.exists(script_path):
                        logger.debug("Script file still exists (%d bytes)", os.path.getsize(script_path))
                    else:
                        logger.warning("Script file no longer exists!")
                        
                    if os.path.exists(request_path):
                        logger.debug("Request file still exists (%d bytes)", os.path.getsize(request_path))
                    else:
                        logger.warning("Request file no longer exists!")
                    
                    last_log_time = current_time
                            
                # Progressive polling - start fast, then slow down
                current_elapsed = time.time() - start_time
                if current_elapsed < 5:
                    poll_interval = 0.1  # First 5 seconds: check every 100ms
                elif current_elapsed < 30:
                    poll_interval = 0.5  # 5-30 seconds: check every 500ms
                else:
                    poll_interval = 1.0  # After 30 seconds: check every second
                
                time.sleep(poll_interval)
            
            # If we've timed out, don't create a fake success - report the timeout as an error
            logger.error("Script execution timed out after %.2f seconds", time.time() - start_time)
            
            # Create an error result file for future reference
            try:
                with open(result_path, 'w', encoding='utf-8') as f:
                    error_result = {
                        "success": False, 
                        "error": "Script execution timed out after {:.2f} seconds".format(time.time() - start_time),
                        "timeout": True
                    }
                    json.dump(error_result, f)
            except Exception as e:
                logger.error("Error creating timeout result file: %s", str(e))
            
            # Clean up files
            # self._cleanup_files(script_path, None, request_path, request_dir)
            
            # Return error response
            return {
                "success": False, 
                "error": "Script execution timed out after {:.2f} seconds".format(time.time() - start_time),
                "timeout": True
            }
            
            # Timeout
            # elapsed = time.time() - start_time
            # logger.error("Script execution timed out after %.2f seconds (%d checks)", elapsed, check_count)
            
            # # Create error result file for reference
            # try:
            #     with open(result_path, 'w', encoding='utf-8') as f:
            #         error_result = {
            #             "success": False,
            #             "error": f"Script execution timed out after {timeout} seconds",
            #             "checks": check_count,
            #             "request_id": request_id
            #         }
            #         json.dump(error_result, f)
            #     logger.debug("Created timeout error result file")
            # except Exception as e:
            #     logger.error("Error creating timeout error result file: %s", str(e))
            
            # # Clean up files but keep script for debugging
            # self._cleanup_files(None, None, request_path, None)
            # logger.info("Kept script file for debugging: %s", script_path)
            
            # return {
            #     "success": False, 
            #     "error": f"Script execution timed out after {timeout} seconds",
            #     "script_path": script_path,
            #     "result_path": result_path,
            #     "request_id": request_id
            # }
        except Exception as e:
            logger.error("Error executing script (request ID: %s): %s", request_id, str(e))
            logger.error(traceback.format_exc())
            # Attempt to clean up files
            # if script_path or result_path or request_path:
            #     self._cleanup_files(script_path, result_path, request_path, request_dir)
            return {"success": False, "error": str(e)}
            
    def _cleanup_files(self, script_path, result_path, request_path, request_dir=None):
        """Clean up temporary files.
        
        Args:
            script_path (str): Path to the script file
            result_path (str): Path to the result file
            request_path (str): Path to the request file
            request_dir (str): Path to the request directory (optional)
        """
        # First clean up individual files
        for path in [script_path, result_path, request_path]:
            if not path:
                continue
                
            try:
                if os.path.exists(path):
                    os.remove(path)
                    # logger.debug("Removed temporary file: %s", path)
            except Exception as e:
                logger.warning("Failed to remove temporary file %s: %s", path, str(e))
        
        # Then clean up the request directory if specified
        if request_dir and os.path.exists(request_dir):
            try:
                # Check if directory is empty
                if not os.listdir(request_dir):
                    os.rmdir(request_dir)
                    # logger.debug("Removed empty request directory: %s", request_dir)
                else:
                    # logger.warning("Request directory not empty, not removing: %s", request_dir)
                    # List files left in directory
                    # logger.debug("Files remaining in request directory: %s", os.listdir(request_dir))
                    ...
            except Exception as e:
                logger.warning("Failed to remove request directory %s: %s", request_dir, str(e))


class ScriptGenerator:
    """Generates scripts for different operations."""
    
    def generate_session_start_script(self):
        """Generate script to start a session."""
        return """
import scriptengine
import json
import sys
import warnings

# Silence deprecation warnings for sys.exc_clear() in IronPython 2.7
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    # Use the global system instance provided by scriptengine
    # IMPORTANT: scriptengine.system is a pre-existing instance
    print("Using global scriptengine.system instance")
    system = scriptengine.system
    
    # Store system instance
    session.system = system
    
    # Return success
    result = {"success": True, "message": "Session started"}
except:
    # IronPython 2.7 style exception handling (no 'as e' syntax)
    error_type, error_value, error_traceback = sys.exc_info()
    result = {"success": False, "error": str(error_value)}
"""
        
    def generate_session_status_script(self):
        """Generate script to get session status."""
        return """
import scriptengine
import json

try:
    # Get system status
    system = session.system
    
    result = {
        "success": True,
        "status": {
            "session_active": system is not None,
            "project_open": session.active_project is not None
        }
    }
    
    if session.active_project:
        result["status"]["project"] = {
            "path": session.active_project.path,
            "dirty": session.active_project.dirty
        }
except Exception as e:
    result = {"success": False, "error": str(e)}
"""
        
    def generate_project_create_script(self, params):
        """Generate script to create a project."""
        path = params.get("path", "")
        # Normalize path to use backslashes for Windows
        path = path.replace("/", "\\")
        
        # Get template_path parameter or build from CODESYS_PATH
        template_path = params.get("template_path", "")
        # if not template_path:
            # # Derive template path from CODESYS executable path
            # codesys_dir = os.path.dirname(CODESYS_PATH)  # Get directory containing CODESYS.exe
            # if "Common" in codesys_dir:  # Handle "Common" subfolder case
            #     codesys_dir = os.path.dirname(codesys_dir)  # Go up one level
            # template_path = os.path.join(codesys_dir, "Templates", "Standard.project")
            # logger.info("Using derived template path: %s", template_path)
            
            
        # Pass CODESYS_PATH to the script to help find templates
        codesys_path = CODESYS_PATH
            
        # Create a super simple script - just open the template and save as the new name
        return """
# Simple script to create a project from template - IronPython 2.7 compatible
import scriptengine
import json
import os
import sys
import warnings
import traceback

# Silence deprecation warnings for sys.exc_clear() in IronPython 2.7
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    print("Starting project creation script")
    
    # Check if standard template exists at the provided path
    template_path = "{1}"
    print("Looking for template at: " + template_path)
    
    if not os.path.exists(template_path):
        print("Template not found at: " + template_path)
        
        # Try to determine template location directly from CODESYS_PATH
        codesys_path = r"{2}"
        print("CODESYS path: " + codesys_path)
        
        # Derive template path from CODESYS executable path
        codesys_dir = os.path.dirname(codesys_path)  # Get directory containing CODESYS.exe
        if "Common" in codesys_dir:  # Handle "Common" subfolder case
            codesys_dir = os.path.dirname(codesys_dir)  # Go up one level
            
        template_path = os.path.join(codesys_dir, "Templates", "Standard.project")
        print("Trying template at: " + template_path)
    
    if not os.path.exists(template_path):
        print("Template not found! Cannot create project from template.")
        raise Exception("Template not found at: " + template_path)
    
    # Simple approach: open template, save as new name
    print("Opening template: " + template_path)
    project = scriptengine.projects.open(template_path)
    if project is None:
        print("Failed to open template project")
        raise Exception("Failed to open template project at: " + template_path)
    
    print("Template opened successfully")
    
    # Save as new project name
    print("Saving as new project: {0}")
    if hasattr(project, 'save_as'):
        project.save_as("{0}")
        print("Project saved successfully as: {0}")
        # That's it! The project is now saved with our desired name and is already the active project
    else:
        print("Project has no save_as method")
        raise Exception("Project object does not have a save_as method")
    
    # Set as active project
    print("Setting as active project")
    session.active_project = project
    
    # Check active application
    print("Checking for active application")
    if hasattr(project, 'active_application') and project.active_application is not None:
        app = project.active_application
        print("Found active application: " + str(app))
    else:
        print("No active application found in project")
    
    print("Project creation completed")
    
    # Return success result
    # Note: Project is already saved to disk at this point (save_as operation handles this)
    # There's no need to call save_project() immediately after create_project()
    result = {{
        "success": True,
        "project": {{
            "path": project.path if hasattr(project, 'path') else "{0}",
            "name": project.name if hasattr(project, 'name') else os.path.basename("{0}"),
            "dirty": project.dirty if hasattr(project, 'dirty') else False
        }}
    }}
except:
    # IronPython 2.7 style exception handling (no 'as e' syntax)
    error_type, error_value, error_traceback = sys.exc_info()
    print("Error creating project: " + str(error_value))
    print(traceback.format_exc())
    
    result = {{
        "success": False,
        "error": str(error_value)
    }}
""".format(path.replace("\\", "\\\\"), template_path.replace("\\", "\\\\"), codesys_path.replace("\\", "\\\\"))
    
    def extract_pou_blocks(self, code: str):
        end_pattern = {
            "FUNCTION_BLOCK": re.compile(r'END_FUNCTION_BLOCK', re.IGNORECASE),
            "FUNCTION": re.compile(r'END_FUNCTION', re.IGNORECASE),
            "PROGRAM": re.compile(r'END_PROGRAM', re.IGNORECASE)
        }

        blocks = []
        pos = 0
        while pos < len(code):
            match = re.search(r'^[ \t]*(FUNCTION_BLOCK|FUNCTION|PROGRAM)\s+(\w+)', code[pos:], re.IGNORECASE | re.MULTILINE)
            if not match:
                break
            block_type, block_name = match.groups()
            start_idx = pos + match.start()
            end_match = end_pattern[block_type.upper()].search(code[start_idx:])
            if not end_match:
                break
            end_idx = start_idx + end_match.end()
            full_block = code[start_idx:end_idx]

            # Separate declaration from implementation using END_VAR marker
            decl_split = full_block.split("END_VAR")
            if len(decl_split) >= 2:
                declaration = "END_VAR".join(decl_split[:-1]) + "END_VAR"
                implementation = decl_split[-1]
                # Remove END_XXX from implementation
                implementation = end_pattern[block_type.upper()].sub('', implementation).strip()
            else:
                declaration = full_block
                implementation = ""

            # Extract return type if FUNCTION
            return_type = ""
            if block_type == "FUNCTION":
                header_snippet = full_block[:200]
                ret_match = re.search(r'FUNCTION\s+\w+\s*:\s*(\w+)', header_snippet, re.IGNORECASE)
                if ret_match:
                    return_type = ret_match.group(1)

            blocks.append({
                "pou_name": block_name,
                "pou_type": block_type,
                "pou_code": (declaration.strip(), implementation.strip()),
                "return_type": return_type
            })
            pos = end_idx

        return blocks
    
    def generate_pou_create_set_compile_script(self, params):
        """Generate script to create, set, and compile a pou."""
        pou_infos = []
        for param in params:
            pou_name = param.get("pou_name", "TestBlock")
            pou_type = param.get("pou_type", "FUNCTION_BLOCK")
            code_decl, code_impl = param.get("pou_code", ("", ""))
            ret_type = param.get("return_type", "")
            # Escape code for string literal
            code_decl = code_decl.replace("\\", "\\\\").replace("\n", "\\n")
            code_impl = code_impl.replace("\\", "\\\\").replace("\n", "\\n")
            pou_info = """
{{
"pou_name": "{0}",
"pou_type": "{1}",
"code_decl": "{2}",
"code_impl": "{3}",
"ret_type": "{4}"
}}
""".format(pou_name, pou_type, code_decl, code_impl, ret_type).strip()
            pou_infos.append(pou_info)
        
        pou_infos_str = "["+ ",\n".join(pou_infos) + "]"
        
        return """# -*- coding: utf-8 -*-
import scriptengine
import traceback
import time
import re

debug_info = "DEBUGGING INFO:\\n"

if not hasattr(session, 'active_project') or session.active_project is None:
    print("No active project in session")
    result = {{"success": False, "error": "No active project in session"}}
    raise Exception("No active project in session")


pou_infos = {0}
result = {{}}
pou_objs = []

pou_mapping = {{
    info["pou_name"]: info["pou_type"] for info in pou_infos
}}

# Try to get application
project = session.active_project
# print("Got active project")
if not hasattr(project, 'active_application') or project.active_application is None:
    # print("Project has no active application")
    result = {{"success": False, "error": "Project has no active application"}}
    raise Exception("Project has no active application")

application = project.active_application
# print("Got active application")

def update_variable_type(program_code, new_types):
    pattern = re.compile(r"(VAR)(.*?)(END_VAR)", re.DOTALL)

    match = pattern.search(program_code)
    if not match:
        raise ValueError("No valid VAR ... END_VAR block found.")

    var_block_start = match.group(1)
    var_block_end = match.group(3)

    new_vars = ["test_{{}}: {{}};".format(i, new_type)
        for i, new_type in enumerate(new_types)
    ]
    new_var_block = "VAR\\n" + "\\n".join(new_vars) + "\\nEND_VAR"

    updated_code = program_code[:match.start()] + new_var_block + program_code[match.end():]
    return updated_code


def get_program(application):
    program_name = "PLC_PRG"
    programs = application.find(program_name)
    if len(programs):
        return programs[0]
    try:
        program = application.create_pou(
            name=program_name,
            type=scriptengine.PouType.Program
        )
        return program
    except Exception, e:
        # print("Error creating program: " + str(e))
        result = {{"success": False, "error": "Error creating program: " + str(e)}}
        raise Exception("Error creating program: " + str(e))


def create_new_pou(project, pou_info):
    name = pou_info.get('pou_name')
    pou_type = pou_info.get('pou_type')
    declaration_text = pou_info.get('code_decl')
    implementation_text = pou_info.get('code_impl')
    ret_type = pou_info.get('ret_type')

    # print("Starting POU creation script for %s" % name)
    application = project.active_application
    # print("Got active application")

    container = application
    # print("Using application object directly for POU creation")
    
    # Use the properly defined POU types and implementation languages
    try:
        # Map the string name to the actual PouType enum value
        # print("Determining POU type for: %s" % pou_type)
        
        # Define POU type map according to the working example code
        pou_type_map = {{
            "PROGRAM": scriptengine.PouType.Program,
            "FUNCTION_BLOCK": scriptengine.PouType.FunctionBlock,
            "FUNCTION": scriptengine.PouType.Function
        }}
        
        # Get the POU type from the map
        if pou_type in pou_type_map:
            pou_type_value = pou_type_map[pou_type]
            # print("Set POU type to %s" % pou_type)
        else:
            # print("Unknown POU type: %s" % pou_type)
            result = {{"success": False, "error": "Unknown POU type: %s" % pou_type}}
            raise Exception("Unknown POU type: %s" % pou_type)
        
        # print("Using default language: ST (None)")
        
    except Exception, e:
        # print("Error resolving type values: " + str(e))
        result = {{"success": False, "error": "Error resolving type values: " + str(e)}}
        return None, result
    
    # Create POU with the correct parameters
    try:
        # print("Creating POU: " + name)
        
        # Call with keyword arguments as shown in the example
        if pou_type == "FUNCTION":
            # For functions, return_type is required
            pou = container.create_pou(
                name=name,
                type=pou_type_value,
                return_type=ret_type
            )
            # print("Created function with return type")
        else:
            # For programs and function blocks, return_type should not be specified
            pou = container.create_pou(
                name=name,
                type=pou_type_value
            )
            # print("Created POU without return type")
        
        if pou is not None:
            # print("POU created successfully")

            pou.textual_declaration.replace(declaration_text)
            pou.textual_implementation.replace(implementation_text)

            # print("POU updated successfully")
            
            result = {{
                "success": True,
                "pou": {{
                    "name": name,
                    "type": pou_type
                }}
            }}
            return pou, result
        else:
            # print("POU creation failed - returned None")
            result = {{"success": False, "error": "POU creation failed - returned None"}}
    except Exception, e:
        # print("Error creating POU: " + str(e))
        result = {{"success": False, "error": "Error creating POU: " + str(e)}}
    
    return None, result


def clean_app(application):
    try:
        # print("Performing clean build")
        
        program_name = "PLC_PRG"
        programs = application.find(program_name)
        if len(programs) > 0:
            plc_prg = programs[0]
            clean_decl = "PROGRAM PLC_PRG\\nVAR\\nEND_VAR"
            try:
                plc_prg.textual_declaration.replace(clean_decl)
                # print("Restored PLC_PRG declaration to clean state")
            except Exception, restore_err:
                # print("Warning: Failed to restore PLC_PRG declaration: " + str(restore_err))
                pass

        # print("Cleaning application")
        all_pous = application.get_children()
        removed_count = 0
        if all_pous:
            for obj in all_pous:
                try:
                    obj_name = obj.get_name()
                    if obj_name != program_name and obj.has_textual_declaration and obj.has_textual_implementation:
                        # print("Removing POU: " + obj_name)
                        obj.remove()
                        removed_count += 1
                except Exception, remove_err:
                    # print("Warning: Failed to remove object: " + str(remove_err))
                    continue
        
        # print("Cleanup completed, removed {{}} objects".format(removed_count))
        
        application.clean()
        # print("Clean operation completed")
    except Exception, clean_error:
        # print("Error during clean operation: " + str(clean_error))
        # print("Will attempt to continue with build anyway")
        pass
    

def compile_pou(application, pou_objs, pou_mapping):
    start_time = time.time()
    # print("Starting compile process...")

    compile_msgs = []

    def extract_line_number(text):
        match = re.search(r'(?:Line|行)[\\s:]*(\\d+)', text)
        if match:
            return int(match.group(1))
        else:
            return -1

    try:
        # print("Compiling application...")
        application.build()
        # print("Compiling operation completed")
        compilation_time = time.time() - start_time
        
        cates = system.get_message_categories(bActive=False)
        for cate in cates:
            if cate is None:
                continue
            desc = system.get_message_category_description(cate)
            build_desc_diff_lang = set(["Build", "编译"])  # supplyment by yourself if need language change
            levels = set([scriptengine.Severity.FatalError, scriptengine.Severity.Error]) # we only consider fatal errors and normal errors
            obj_names = set([obj.get_name() for obj in pou_objs])
            if desc in build_desc_diff_lang:
                # print("Found compile message category, msgs:")
                msg_objs = system.get_message_objects(category=cate)
                # for obj in msg_objs:
                #     print("Obj pos: {{}}, desc: {{}}, ser: {{}}".format(obj.position_text, obj.text, obj.severity))
                compile_msgs = [
                    {{
                        "Path": extract_line_number(obj.position_text),
                        "ErrorDesc": obj.text,
                        "IsDef": True if "Decl" in obj.position_text else False,
                        "PouName": obj.object.get_name() if obj.object else "",
                        "ID": obj.prefix + "{{:0>4d}}".format(int(obj.number))
                    }}
                    for obj in msg_objs if obj.severity in levels and \\
                        obj.object and obj.object.get_name() in obj_names
                ]
                # print(compile_msgs)

        result = {{
            "success": True,
            "message": "Build operation completed",
            "pous": [{{
                "name": pou.get_name(),
                "type": pou_mapping[pou.get_name()]
            }} for pou in pou_objs],
            "time": compilation_time,
            "Errors": compile_msgs
        }}
    except Exception, precompile_error:
        # print("Error during precompile operation: " + str(precompile_error))
        # print(traceback.format_exc())
        result = {{"success": False, "error": "Error during precompile operation: " + str(precompile_error)}}

    return result

try:
    for pou_info in pou_infos:
        pou_obj, result = create_new_pou(project, pou_info)
        if not result["success"]:
            result["error"] += " POU creation failed"
            raise Exception("")
        pou_objs.append(pou_obj)
    
    # We'll try to make a reference of this pou in a default program to ensure pou be compiled in application.build()
    program_obj = get_program(application)
    new_textual_declaration = update_variable_type(
        program_obj.textual_declaration.text, [pou.get_name() for pou in pou_objs])
    program_obj.textual_declaration.replace(new_textual_declaration)
    
    result = compile_pou(application, pou_objs, pou_mapping)
    if not result["success"]:
        result["error"] += " POU compilation failed"
        raise Exception("")
    
except Exception, err:
    # print("Error during workflow: " + str(err))
    # print(traceback.format_exc())
    if not result:
        result = {{"success": False, "error": "Error during workflow: " + str(err)}}

finally:
    # for pou_obj in pou_objs:
    #     if pou_obj is not None:
    #         pou_obj.remove()
    clean_app(application)
    # if project is not None:
    #     project.save()

""".format(pou_infos_str)


class ApiKeyManager:
    """Manages API keys for authentication."""
    
    def __init__(self, key_file_path):
        self.key_file_path = key_file_path
        self.keys = self._load_keys()
        
    def _load_keys(self):
        """Load API keys from file."""
        if not os.path.exists(self.key_file_path):
            # Create default admin key
            keys = {"admin": {"name": "Admin", "created": time.time()}}
            self._save_keys(keys)
            return keys
            
        try:
            with open(self.key_file_path, 'r') as f:
                return json.loads(f.read())
        except:
            return {}
            
    def _save_keys(self, keys):
        """Save API keys to file."""
        with open(self.key_file_path, 'w') as f:
            f.write(json.dumps(keys))
            
    def validate_key(self, key):
        """Validate an API key."""
        return key in self.keys


class CodesysApiHandler(BaseHTTPRequestHandler):
    """HTTP request handler for CODESYS API."""
    
    server_version = "CodesysApiServer/0.1"
    
    def __init__(self, *args, **kwargs):
        self.process_manager = kwargs.pop('process_manager', None)
        self.script_executor = kwargs.pop('script_executor', None)
        self.script_generator: ScriptExecutor = kwargs.pop('script_generator', None)
        self.api_key_manager = kwargs.pop('api_key_manager', None)
        BaseHTTPRequestHandler.__init__(self, *args, **kwargs)
        
    def do_GET(self):
        """Handle GET requests."""
        try:
            # Parse URL
            parsed_url = urlparse.urlparse(self.path)
            path = parsed_url.path.strip('/')
            query = urlparse.parse_qs(parsed_url.query)
            
            # Single-value query params
            params = {}
            for key, values in query.items():
                if values:
                    params[key] = values[0]
                    
            # Check authentication
            if not self.authenticate():
                self.send_error(401, "Unauthorized")
                return
                
            # Route request
            if path == "api/v1/session/status":
                self.handle_session_status()
            else:
                self.send_error(404, "Not Found")
        except ConnectionAbortedError as e:
            logger.warning("Connection aborted during GET request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except BrokenPipeError as e:
            logger.warning("Broken pipe during GET request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except ConnectionResetError as e:
            logger.warning("Connection reset during GET request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except Exception as e:
            logger.error("Error handling GET request: %s", str(e))
            try:
                self.send_error(500, str(e))
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                # Connection already closed, can't send error
                pass
            
    def do_POST(self):
        """Handle POST requests."""
        try:
            # Parse URL
            parsed_url = urlparse.urlparse(self.path)
            path = parsed_url.path.strip('/')
            
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            
            # Python 3 compatibility for reading binary data
            if sys.version_info[0] >= 3:
                post_data = self.rfile.read(content_length).decode('utf-8')
            else:
                post_data = self.rfile.read(content_length)
            
            params = {}
            if content_length > 0:
                params = json.loads(post_data)
                
            # Check authentication
            if not self.authenticate():
                self.send_error(401, "Unauthorized")
                return
                
            # Route request
            if path == "api/v1/session/start":
                self.handle_session_start()
            elif path == "api/v1/project/create":
                self.handle_project_create(params)
            elif path == "api/v1/pou/workflow":
                self.handle_pou_compile_workflow(params)
            else:
                self.send_error(404, "Not Found")
        except ConnectionAbortedError as e:
            logger.warning("Connection aborted during POST request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except BrokenPipeError as e:
            logger.warning("Broken pipe during POST request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except ConnectionResetError as e:
            logger.warning("Connection reset during POST request: %s", str(e))
            # Don't try to send an error response as the connection is already broken
        except Exception as e:
            logger.error("Error handling POST request: %s", str(e))
            try:
                self.send_error(500, str(e))
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                # Connection already closed, can't send error
                pass
            
    def authenticate(self):
        """Validate API key."""
        auth_header = self.headers.get('Authorization', '')
        
        if auth_header.startswith('ApiKey '):
            api_key = auth_header[7:]  # Remove 'ApiKey ' prefix
            return self.api_key_manager.validate_key(api_key)
            
        return False
        
    def send_json_response(self, data, status=200):
        """Send JSON response."""
        try:
            response = json.dumps(data)
            
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            
            # Python 3 compatibility for content length
            if sys.version_info[0] >= 3:
                self.send_header('Content-Length', len(response.encode('utf-8')))
            else:
                self.send_header('Content-Length', len(response))
                
            self.end_headers()
            
            # Python 3 compatibility for writing binary data
            if sys.version_info[0] >= 3:
                self.wfile.write(response.encode('utf-8'))
            else:
                self.wfile.write(response)
        except ConnectionAbortedError as e:
            logger.warning("Connection aborted while sending response: %s", str(e))
        except BrokenPipeError as e:
            logger.warning("Broken pipe while sending response: %s", str(e))
        except ConnectionResetError as e:
            logger.warning("Connection reset while sending response: %s", str(e))
        except Exception as e:
            logger.error("Error sending JSON response: %s", str(e))
        
    # Handler methods
    
    def handle_session_start(self):
        """Handle session/start endpoint."""
        try:
            logger.info("Session start requested - checking CODESYS process")
            
            # First check if the process is already running
            if self.process_manager.is_running():
                logger.info("CODESYS process already running, using existing process")
            else:
                logger.info("CODESYS process not running, attempting to start")
                
                # Start the CODESYS process
                if not self.process_manager.start():
                    error_msg = "Failed to start CODESYS process"
                    logger.error(error_msg)
                    self.send_json_response({
                        "success": False,
                        "error": error_msg
                    }, 500)
                    return
                    
                logger.info("CODESYS process started successfully")
            
            # Generate the session start script
            script = self.script_generator.generate_session_start_script()
            
            # Execute the script to properly initialize the session
            logger.info("Executing session start script in CODESYS")
            result = self.script_executor.execute_script(script)
            
            # Return the result from the script execution
            self.send_json_response(result)
            
            # Remove all the commented out code that was causing indentation errors
                
        except Exception as e:
            logger.error("Unhandled error in session start: %s", str(e), exc_info=True)
            self.send_json_response({
                "success": False,
                "error": f"Internal server error: {str(e)}"
            }, 500)
            
    def handle_session_status(self):
        """Handle session/status endpoint."""
        # Check process status
        process_running = self.process_manager.is_running()
        process_status = self.process_manager.get_status()
        
        # Execute the script to get actual session status
        if process_running:
            script = self.script_generator.generate_session_status_script()
            logger.info("Executing session status script in CODESYS")
            status_result = self.script_executor.execute_script(script)
            
            if status_result.get("success", False) and "status" in status_result:
                session_status = status_result["status"]
            else:
                session_status = {"active": process_running, "session_active": process_running, "project_open": False}
        else:
            session_status = {"active": False, "session_active": False, "project_open": False}
                
        # Combine status information
        status = {
            "process": {
                "running": process_running,
                "state": process_status.get("state", "unknown"),
                "timestamp": process_status.get("timestamp", time.time())
            },
            "session": session_status
        }
        
        self.send_json_response({
            "success": True,
            "status": status
        })
        
    def handle_project_create(self, params):
        """Handle project/create endpoint."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if "path" not in params:
            # If path is not provided, use the current directory
            default_path = os.path.join(script_dir, f"projects/CODESYS_Test_Project.project")
            logger.info("No path provided, using default path: %s", default_path)
            params["path"] = default_path
        
        # Allow specifying a template path (optional)
        template_path = params.get("template_path", "")
        if template_path:
            logger.info("Using template from: %s", template_path)
        else:
            logger.info("No template specified, will try to use standard template")
            template_path = os.path.join(script_dir, "template/Standard.project")
            logger.info("Using derived template path: %s", template_path)
            params["template_path"] = template_path
        
        path = params.get("path", "")
        # Normalize path to use backslashes for Windows
        path = path.replace("/", "\\")
        logger.info("Project creation request for path: %s (executing script in CODESYS)", path)
        
        # Make sure CODESYS is running and fully initialized
        if not self.process_manager.is_running():
            logger.warning("CODESYS not running, attempting to start it")
            if not self.process_manager.start():
                error_msg = "Failed to start CODESYS process"
                logger.error(error_msg)
                self.send_json_response({
                    "success": False,
                    "error": error_msg
                }, 500)
                return
            # The start method now includes a wait for full initialization
        
        # Generate the script (IronPython 2.7 compatible)
        script = self.script_generator.generate_project_create_script(params)
        
        logger.info("Executing project creation script in CODESYS")
        # Execute the script with a reasonable timeout
        result = self.script_executor.execute_script(script, timeout=30)
        
        logger.info("Script execution result: %s", result)
        
        if result.get("success", False):
            logger.info("Project creation successful")
            self.send_json_response(result)
        else:
            error_msg = result.get("error", "Unknown error")
            logger.error("Error creating project: %s", error_msg)
            
            # Send error response
            self.send_json_response({
                "success": False,
                "error": error_msg
            }, 500)
        
    def handle_pou_compile_workflow(self, params):
        """Handle POU code compiling workflow"""
        required = ["Code"]
        for field in required:
            if field not in params:
                self.send_json_response({
                    "success": False,
                    "error": "Missing required parameter: " + field
                }, 400)
                return
        
        # params["name"] = params.get("BlockName", "")
        # params["code"] = params.get("Code", "")

        # pou_name = params.get("name", "")
        # logger.info("POU code compiling workflow request for '%s' (executing script in CODESYS)", pou_name)

        # if "type" not in params:
        #     params["type"] = "FunctionBlock" if "FUNCTION_BLOCK" in params["code"] else "Function"
        
        # if params["type"] == "Function" and "return_type" not in params:
        #     pattern = re.compile(r'\bFUNCTION\b\s+\w+\s*:\s*(\w+)', re.IGNORECASE)
        #     match = pattern.search(params["code"])
        #     if match:
        #         params["return_type"] = match.group(1)
        #     else:
        #         self.send_json_response({
        #             "success": False,
        #             "error": "Missing required component in: return_type in code for Function.",
        #             "errors": [{"IsDef": True, "Path": -1, "ErrorDesc": "Missing required component: return_type in code for Function."}]
        #         }, 200)
        #         return

        # Generate and execute POU code setting script
        # script = self.script_generator.generate_pou_create_set_compile_script(params)
        # result = self.script_executor.execute_script(script)
        
        # required = ["pou_name", "pou_type", "pou_code", "return_type"]
        blocks = self.script_generator.extract_pou_blocks(params["Code"])
        result = {
            "success": False,
            "message": "Unknown error"
        }
        script = self.script_generator.generate_pou_create_set_compile_script(blocks)
        result = self.script_executor.execute_script(script)
        if result.get("success", False):
            logger.info("POU code compiling workflow successful")
            result["Success"] = False if "Errors" in result and len(result["Errors"]) > 0 else True
            result["Result"] = result.get("message", "") + result.get("error", "")
            self.send_json_response(result)
        else:
            error_msg = result.get("error", "Unknown error")
            logger.error("Error compiling workflow POU code: %s", error_msg)
            self.send_json_response({
                "success": False,
                "error": error_msg
            }, 500)


# def run_server():
#     """Run the HTTP server."""
#     try:
#         # Create managers
#         process_manager = CodesysProcessManager(CODESYS_PATH, PERSISTENT_SCRIPT)
#         script_executor = ScriptExecutor(REQUEST_DIR, RESULT_DIR)
#         script_generator = ScriptGenerator()
#         api_key_manager = ApiKeyManager(API_KEY_FILE)
        
#         # Create server
#         def handler(*args):
#             return CodesysApiHandler(
#                 process_manager=process_manager,
#                 script_executor=script_executor,
#                 script_generator=script_generator,
#                 api_key_manager=api_key_manager,
#                 *args
#             )
            
#         server = HTTPServer((SERVER_HOST, SERVER_PORT), handler)
        
#         print("Starting server on {0}:{1}".format(SERVER_HOST, SERVER_PORT))
#         logger.info("Starting server on %s:%d", SERVER_HOST, SERVER_PORT)
        
#         # Run server
#         server.serve_forever()
#     except KeyboardInterrupt:
#         print("Server stopped")
#     except Exception as e:
#         print("Error starting server: " + str(e))
#         logger.error("Error starting server: %s", str(e))
#     finally:
#         # Stop CODESYS process
#         if 'process_manager' in locals():
#             process_manager.stop()
            

# if __name__ == "__main__":
#     run_server()
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import time
import os
import json
from datetime import datetime
import sys

import redis


r = redis.Redis.from_url("redis://default:Agl91T4oiFyh9RxiqBFm7DSkPvWy2VAg@redis-16165.c291.ap-southeast-2-1.ec2.redns.redis-cloud.com:16165")

class FileWatcher(FileSystemEventHandler):
    def __init__(self, file_path):
        self.file_path = file_path
        self.player_name = self.get_player_name()
        self.check_count = 0
        self.last_position = self.get_file_size()
        self.events_sent = 0  # Track number of events sent
        self.last_heartbeat = 0  # Track last heartbeat time
        print(f"Detected player name: {self.player_name}")
        
        # Send initial heartbeat immediately
        # self.send_heartbeat()
        
        self.events = []  # Keep this as we still use it for tracking
        
    def get_file_size(self):
        try:
            return os.path.getsize(self.file_path)
        except:
            return 0
            
    def load_existing_events(self):
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, 'r') as f:
                    data = json.load(f)
                    # If file exists but is from a different player, start fresh
                    if data.get("player") != self.player_name:
                        return []
                    return data.get("events", [])
            except:
                pass
        return []
        
    def get_timestamp_from_line(self, line):
        try:
            # Log format is like: <2024-03-19T12:34:56.789Z>
            timestamp_str = line[1:24]  # Extract <YYYY-MM-DDThh:mm:ss.sssZ>
            return timestamp_str
        except:
            # Use timezone-aware UTC time
            return ''
        
    def save_event(self, event_type, details, timestamp=None):
        if timestamp is None:
            timestamp = ''
            
        event = {
            "timestamp": timestamp,
            "player": self.player_name,
            "type": event_type,
            "details": details
        }
        
        try:
            # Convert event to JSON string
            event_json = json.dumps(event)
            
            # Push to Redis list
            if r.rpush("star_citizen_events", event_json):
                # Only print successful events
                print(f"[{timestamp}] Event: {event_type} | Player: {self.player_name} | Details: {json.dumps(details)}")
                self.events_sent += 1
                
        except Exception as e:
            pass  # Silently handle errors
        
    def send_heartbeat(self):
        self.save_event("heartbeat", {
            "status": "online",
            "player": self.player_name
        })
        self.last_heartbeat = time.time()
        
    def check_file(self):
        current_time = time.time()
        
        # Send heartbeat with current UTC time
        if current_time - self.last_heartbeat >= 60:
            self.send_heartbeat()
            
        self.check_count += 1
        try:
            current_size = os.path.getsize(self.file_path)
            if current_size < self.last_position:
                print(f"File was truncated, resetting position from {self.last_position} to 0")
                self.last_position = 0
                
            if current_size == self.last_position:
                return
                
            print(f"Reading file from position {self.last_position} to {current_size}")
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as file:
                file.seek(self.last_position)
                new_lines = file.readlines()
                self.last_position = file.tell()
                
                print(f"Found {len(new_lines)} new lines to process")
                for line in new_lines:
                    timestamp = self.get_timestamp_from_line(line)
                    
                    # Debug print for each line
                    print(f"Processing line: {line[:100]}...")  # Print first 100 chars of line
                    
                    # Check for player connection
                    if "<Expect Incoming Connection>" in line:
                        try:
                            nickname = line.split('nickname="')[1].split('"')[0]
                            session = line.split('session=')[1].split(' ')[0]
                            player_geid = line.split('playerGEID=')[1].split(' ')[0]
                            # Use nickname as the player name
                            self.player_name = nickname
                            self.save_event("connection", {
                                "session": session,
                                "player_geid": player_geid
                            }, timestamp)
                        except:
                            print("Failed to parse connection event")
                    
                    # Check for location updates
                    if f"Player[{self.player_name}]" in line and "Location[" in line:
                        location = line[line.find("Location["):].split("]")[0] + "]"
                        self.save_event("location", {"location": location}, timestamp)
                    
                    # Check for deaths
                    if "<Actor Death>" in line and self.player_name in line:
                        try:
                            victim = line.split("'")[1]
                            killer = line.split("killed by '")[1].split("'")[0]
                            damage_type = line.split("damage type '")[1].split("'")[0]
                            
                            if self.player_name == victim:
                                if victim == killer:
                                    self.save_event("death", {"type": "self", "cause": damage_type}, timestamp)
                                else:
                                    self.save_event("death", {"type": "killed", "killer": killer, "cause": damage_type}, timestamp)
                            elif self.player_name == killer:
                                self.save_event("kill", {"victim": victim, "cause": damage_type}, timestamp)
                        except:
                            self.save_event("death", {"type": "unknown"}, timestamp)
                    
                    # Check for ship entry
                    if "Entity [" in line and f"m_ownerGEID[{self.player_name}]" in line:
                        try:
                            ship_type = line.split("Entity [")[1].split("]")[0]
                            if ship_type.startswith(("AEGS", "ANVL", "CRUS", "MISC", "RSI")) and "_" in ship_type:
                                self.save_event("ship_entry", {"ship": ship_type.replace('_', ' ')}, timestamp)
                        except:
                            pass
                            
        except Exception as e:
            print(f"Error reading file: {str(e)}")

    def get_player_name(self):
        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as file:
                for line in file:
                    if "<AccountLoginCharacterStatus_Character>" in line and "name " in line:
                        # Extract name from the line
                        name = line.split("name ")[1].split(" -")[0]
                        return name
            print("Error: Could not find player name in log file!")
            sys.exit(1)  # Exit program if no name found
        except Exception as e:
            print(f"Error getting player name: {str(e)}")
            sys.exit(1)  # Exit program on error

def load_config():
    # Get AppData path
    app_data_path = os.path.join(os.getenv('APPDATA'), 'SCCommand')
    config_path = os.path.join(app_data_path, 'game_log_path.txt')
    
    # Create directory if it doesn't exist
    os.makedirs(app_data_path, exist_ok=True)
    
    try:
        with open(config_path, 'r') as f:
            return f.read().strip()
    except:
        print("Error: Game.log path not found!")
        print("Please reinstall the application")
        input("Press Enter to exit...")
        sys.exit(1)

def main():
    file_to_watch = load_config()
    
    print("\nSC Command - Star Citizen Event Tracker")
    print("=" * 40)
    print(f"Target file: {file_to_watch}")
    
    if not os.path.exists(file_to_watch):
        print(f"Error: File {file_to_watch} does not exist!")
        print(f"Please check the path in config.json")
        input("Press Enter to exit...")
        return

    # Test Redis connection more thoroughly
    try:
        print("Testing Redis connection...")
        r.ping()
        
        # Try a test write/read
        test_key = "sc_watcher_test"
        test_value = "connection_test"
        if not r.set(test_key, test_value):
            raise Exception("Failed to write test value to Redis")
        
        read_value = r.get(test_key)
        if not read_value or read_value.decode('utf-8') != test_value:
            raise Exception("Failed to read test value from Redis")
            
        r.delete(test_key)
        print("Redis connection successful!")
        
    except Exception as e:
        print(f"Redis Connection Error: {str(e)}")
        print("Please check your Redis connection")
        input("Press Enter to exit...")
        return

    try:
        watcher = FileWatcher(file_to_watch)
        print("\nTracking events for player:")
        print(f">>> {watcher.player_name} <<<")
        print("\nEvents will be sent to Redis")
        print("\nPress Ctrl+C to stop...")
        
        while True:
            try:
                watcher.check_file()  # This now includes heartbeat check
                time.sleep(10)
            except redis.RedisError as e:
                print(f"Redis Error during check: {str(e)}")
                time.sleep(30)  # Wait longer on Redis error
            except Exception as e:
                print(f"Error during check: {str(e)}")
                time.sleep(10)
                
    except KeyboardInterrupt:
        print(f"\nFile watching stopped. Total events sent: {watcher.events_sent}")
    finally:
        input("Press Enter to exit...")

if __name__ == "__main__":
    main()

import socket
import struct
import sys
import select
import time
import gpiod
import subprocess
from typing import Dict, List, Optional, Tuple

class GPIOReader:
    def __init__(self, pin_numbers: List[int]):
        self.pin_numbers = pin_numbers
        self.pin_mappings: Dict[int, Tuple[gpiod.Chip, gpiod.Line]] = {}
        self._setup_gpio()

    def _find_gpio_chip_and_line(self, pin_number: int) -> Tuple[str, int]:
        try:
            result = subprocess.run(
                ['gpiofind', f'PIN_{pin_number}'], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            chip_name, line = result.stdout.strip().split()
            return chip_name, int(line)
            
        except subprocess.CalledProcessError as e:
            raise ValueError(f"Could not find GPIO chip for pin {pin_number}: {str(e)}")

    def _setup_gpio(self) -> None:
        try:
            for pin in self.pin_numbers:
                chip_name, line_offset = self._find_gpio_chip_and_line(pin)
                print(f"Mapped PIN_{pin} to {chip_name} line {line_offset}")
                
                chip = gpiod.Chip(chip_name)
                line = chip.get_line(line_offset)
                line.request(consumer=f"gpio_reader_pin_{pin}", type=gpiod.LINE_REQ_DIR_IN)
                
                self.pin_mappings[pin] = (chip, line)

        except Exception as e:
            self.cleanup()
            raise RuntimeError(f"Failed to initialize GPIO: {str(e)}") from e

    def read_all(self) -> Dict[int, int]:
        values = {}
        for pin in self.pin_numbers:
            _, line = self.pin_mappings[pin]
            values[pin] = line.get_value()
        return values

    def cleanup(self) -> None:
        for chip, _ in self.pin_mappings.values():
            chip.close()
        self.pin_mappings.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

def send_msp_message(ip, port, direction, cmd, values):
    """Send an MSP message with a specific command and payload to the given UDP address."""
    payload = values

    header = ('$M' + direction).encode('utf-8')
    size = len(payload)
    checksum = size ^ cmd
    for byte in payload:
        checksum ^= byte
    message = header + bytes([size]) + bytes([cmd]) + payload + bytes([checksum])

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(message, (ip, port))
        print(f"Sent MSP message to {ip}:{port}: {message.hex()}")

def non_blocking_input():
    """Returns non-blocking input from stdin."""
    rlist, _, _ = select.select([sys.stdin], [], [], 0)
    if rlist:
        return sys.stdin.readline().strip()
    return ""

def send_initial_setup(target_ip, target_port):
    """Send the initial setup commands."""
    print("Sending initial setup commands...")
    subcommand = [0]
    
    # Initial clear and draw commands
    subcommand[0] = 2
    for _ in range(100):
        subcommand[0] = 2
        send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
        subcommand[0] = 4
        send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))        

    # Send initial subcommand
    subcommand[0] = 0
    send_msp_message(target_ip, target_port, '>', 101, bytes(subcommand))
    print("Initial setup complete")

def main():
    # Configuration
    target_ip = "127.0.0.1"
    target_port = 14551
    cmd = 105

    # Key-to-Values Mapping (WASDMX)
    key_mapping = {
        'w': [1500, 2000, 1500, 1500],
        'a': [2000, 1500, 1500, 1500],
        's': [1500, 1000, 1500, 1500],
        'd': [2000, 1500, 1500, 1500],
        'm': [2000, 1000, 1000, 1000],
        'x': [1500, 1500, 1000, 1500],
        '0': [1500, 1500, 1500, 1500],
    }

    # GPIO Pins Mapping
    gpio_mapping = {
        16: 'w',  # GPIO16 maps to 'w' key
        13: 'a',  # GPIO13 maps to 'a' key
        18: 's',  # GPIO18 maps to 's' key
        11: 'd',  # GPIO11 maps to 'd' key
        32: 'm',  # GPIO32 maps to 'm' key
        38: 'x',  # GPIO38 maps to 'x' key
    }

    # Default payload (16 values with fixed remaining values as 1500)
    values = [1500] * 16
    
    print("Control the MSP sender using WASDMX keys or GPIO buttons.")
    print("Enter 'w', 'a', 's', 'd', 'm', or 'x' to control.")
    print("Press 'q' to quit.")
    print("Waiting for first input to initialize...")
    
    # Initialize GPIO reader
    pin_numbers = list(gpio_mapping.keys())
    
    # Flag to track if initialization has been done
    initialized = False

    try:
        with GPIOReader(pin_numbers) as gpio:
            last_gpio_states = {pin: 0 for pin in pin_numbers}
            
            while True:
                # Check GPIO inputs
                gpio_states = gpio.read_all()
                first_input = False
                
                for pin, state in gpio_states.items():
                    # Check for rising edge (button press)
                    if state == 1 and last_gpio_states[pin] == 0:
                        if not initialized:
                            first_input = True
                        
                        key = gpio_mapping[pin]
                        print(f"GPIO PIN_{pin} pressed, simulating key '{key}'")
                        
                        # Update values based on the GPIO input
                        values[:4] = key_mapping[key]
                        values[4:] = [1500] * 12
                        
                        if first_input:
                            send_initial_setup(target_ip, target_port)
                            initialized = True
                            
                        # Send the commands
                        subcommand = [0]
                        
                        subcommand[0] = 0
                        send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
                        
                        subcommand[0] = 2
                        send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
                        
                        send_msp_message(target_ip, target_port, '<', cmd, struct.pack(f"<{len(values)}H", *values))
                        
                        # Reset sticks to default
                        values[:4] = key_mapping['0']
                        values[4:] = [1500] * 12            
                        send_msp_message(target_ip, target_port, '<', cmd, struct.pack(f"<{len(values)}H", *values))
                        
                        subcommand[0] = 4
                        send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
                
                # Update last known GPIO states
                last_gpio_states = gpio_states

                # Check keyboard input
                user_input = non_blocking_input()
                if user_input == 'q':
                    print("Exiting sender.")
                    break
                
                if user_input in key_mapping:
                    if not initialized:
                        send_initial_setup(target_ip, target_port)
                        initialized = True
                    
                    values[:4] = key_mapping[user_input]
                    values[4:] = [1500] * 12
                    print(f"Key '{user_input}' pressed: {values}")

                    subcommand = [0]
                    subcommand[0] = 0
                    send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
                    
                    subcommand[0] = 2
                    send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))
                    
                    send_msp_message(target_ip, target_port, '<', cmd, struct.pack(f"<{len(values)}H", *values))
                    
                    values[:4] = key_mapping['0']
                    values[4:] = [1500] * 12            
                    send_msp_message(target_ip, target_port, '<', cmd, struct.pack(f"<{len(values)}H", *values))
                    
                    subcommand[0] = 4
                    send_msp_message(target_ip, target_port, '>', 182, bytes(subcommand))

                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    main()
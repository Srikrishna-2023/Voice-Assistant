import threading
import sys
from pystray import Icon, MenuItem, Menu
from PIL import Image
import assistant  # import your assistant module

# Start assistant in a thread
def run_assistant():
    assistant.enhanced_assistant()

# Exit app
def exit_action(icon, item):
    icon.stop()
    sys.exit()

def setup_tray():
    # Load your icon image file here
    image = Image.open("image.png")

    icon = Icon("Byte", image, menu=Menu(
        MenuItem("Exit", exit_action)
    ))

    # Run assistant in background thread
    threading.Thread(target=run_assistant, daemon=True).start()
    
    icon.run()

if __name__ == "__main__":      
    setup_tray()

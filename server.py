from flask import Flask, render_template
import threading
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

def start_assistant():
    from assistant import enhanced_assistant
    enhanced_assistant(wake_word="jarvis")

if __name__ == '__main__':
    # Start assistant in background thread
    assistant_thread = threading.Thread(target=start_assistant, daemon=True)
    assistant_thread.start()
    # Start Flask
    app.run(host='0.0.0.0', port=5000, debug=False)
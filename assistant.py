import os
import subprocess
import speech_recognition as sr
import winsound
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import time
import dateparser
import threading
import datetime
import pickle
import nltk
from nltk.stem import WordNetLemmatizer
from nltk.tokenize import word_tokenize
import psutil
import struct
import re
import queue
import win32com.client
from dotenv import load_dotenv
load_dotenv()
from websocket_bridge import AssistantIntegration

bridge = AssistantIntegration(None)
threading.Thread(target=bridge.start_server, daemon=True).start()

PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_KEY")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
OPENWEATHER_KEY = os.getenv("OPENWEATHER_KEY")

# ─── NLTK ─────────────────────────────────────────────────────────────────────

nltk_data_path = os.path.join(os.path.expanduser('~'), 'nltk_data')
if nltk_data_path not in nltk.data.path:
    nltk.data.path.append(nltk_data_path)

for resource, name in [('tokenizers/punkt_tab', 'punkt_tab'),
                        ('corpora/wordnet', 'wordnet'),
                        ('tokenizers/punkt', 'punkt')]:
    try:
        nltk.data.find(resource)
    except LookupError:
        nltk.download(name, quiet=True)

lemmatizer = WordNetLemmatizer()
recognizer = sr.Recognizer()

# ─── Intent Model ─────────────────────────────────────────────────────────────

_model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'intent_model.pkl')
try:
    with open(_model_path, 'rb') as f:
        model, vectorizer = pickle.load(f)
    print(f"[Intent Model] Loaded from {_model_path}")
except Exception as e:
    print(f"[Intent Model] WARNING: Could not load model: {e}")
    model, vectorizer = None, None

# ─── TTS via win32com (Windows SAPI - most reliable on Windows) ───────────────

_tts_queue = queue.Queue()

def _tts_worker():
    import pythoncom
    pythoncom.CoInitialize()
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.Rate = 1   # -10 (slow) to 10 (fast), 1 is slightly faster than default
    speaker.Volume = 100
    while True:
        text = _tts_queue.get()
        if text is None:
            break
        try:
            speaker.Speak(text)
        except Exception as e:
            print(f"[TTS] Error: {e}")
        finally:
            _tts_queue.task_done()

_tts_thread = threading.Thread(target=_tts_worker, daemon=True)
_tts_thread.start()

# ─── Wake Word ────────────────────────────────────────────────────────────────

def listen_for_wake_word(keyword="jarvis", access_key=PICOVOICE_ACCESS_KEY):
    import pvporcupine
    import pyaudio

    try:
        porcupine = pvporcupine.create(
            access_key=access_key,
            keywords=[keyword]
        )
    except Exception as e:
        print(f"[Porcupine] Failed to init: {e}")
        print("[Porcupine] Falling back to keyboard input - press Enter to activate.")
        input("Press Enter to activate JARVIS...")
        return

    pa = pyaudio.PyAudio()
    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length
    )

    print(f"[Porcupine] Listening for wake word: '{keyword}'")

    try:
        while True:
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)
            result = porcupine.process(pcm)
            if result >= 0:
                print("[Porcupine] Wake word detected!")
                break
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        porcupine.delete()

# ─── NLP / Intent ─────────────────────────────────────────────────────────────

def preprocess_text(text):
    tokens = word_tokenize(text)
    tokens = [lemmatizer.lemmatize(token.lower()) for token in tokens]
    return ' '.join(tokens)

def predict_intent(text):
    if model is None or vectorizer is None:
        return "unknown", 0.0
    processed = preprocess_text(text)
    vector = vectorizer.transform([processed])
    prediction = model.predict(vector)
    probability = model.predict_proba(vector)
    confidence = max(probability[0])
    print(f"Intent: {prediction[0]}, Confidence: {confidence:.2f}")
    return prediction[0], confidence

# ─── Spotify ──────────────────────────────────────────────────────────────────

REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
_cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.spotipy_cache')

def get_spotify_client():
    try:
        sp_oauth = SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE,
            open_browser=False,
            cache_path=_cache_path
        )
        token_info = sp_oauth.get_cached_token()
        if not token_info:
            auth_url = sp_oauth.get_authorize_url()
            print("\nGo to the following URL and authorize the app:")
            print(auth_url)
            redirected_url = input("Paste the full redirect URL here:\n")
            code = sp_oauth.parse_response_code(redirected_url)
            token_info = sp_oauth.get_access_token(code, as_dict=True)
        elif sp_oauth.is_token_expired(token_info):
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
        access_token = token_info['access_token']
        return spotipy.Spotify(auth=access_token)
    except Exception as e:
        print(f"[Spotify] Auth error: {e}")
        return None

def get_active_device_id(sp):
    if sp is None:
        return None
    try:
        devices = sp.devices()
        for device in devices['devices']:
            if device['is_active']:
                return device['id']
        return devices['devices'][0]['id'] if devices['devices'] else None
    except Exception as e:
        print(f"[Spotify] Device error: {e}")
        return None

def handle_spotify_commands(command, sp):
    if sp is None:
        return "Spotify is not connected."

    device_id = get_active_device_id(sp)
    if not device_id:
        return "Spotify is not open or no active device found."

    command_lower = command.lower()

    if "pause" in command_lower:
        try:
            sp.pause_playback(device_id=device_id)
            return "Playback paused."
        except Exception as e:
            return f"Could not pause: {e}"

    if "resume" in command_lower or command_lower.strip() == "play":
        try:
            sp.start_playback(device_id=device_id)
            return "Resuming playback."
        except Exception as e:
            return f"Could not resume: {e}"

    if "play" in command_lower:
        query = command_lower.replace("play", "").strip()
        if not query:
            try:
                sp.start_playback(device_id=device_id)
                return "Resuming playback."
            except Exception as e:
                return f"Could not start playback: {e}"

        if " by " in query:
            parts = query.split(" by ")
            track_name = parts[0].strip()
            artist_name = parts[1].strip()
            search_query = f"track:{track_name} artist:{artist_name}"
        else:
            search_query = query

        try:
            track_results = sp.search(q=search_query, type='track', limit=10)
            if track_results['tracks']['items']:
                track = max(track_results['tracks']['items'], key=lambda t: t['popularity'])
                sp.start_playback(device_id=device_id, uris=[track['uri']])
                return f"Playing track: {track['name']} by {track['artists'][0]['name']}."

            playlist_results = sp.search(q=search_query, type='playlist', limit=1)
            if playlist_results['playlists']['items']:
                playlist = playlist_results['playlists']['items'][0]
                sp.start_playback(device_id=device_id, context_uri=playlist['uri'])
                return f"Playing playlist: {playlist['name']}."

            album_results = sp.search(q=search_query, type='album', limit=1)
            if album_results['albums']['items']:
                album = album_results['albums']['items'][0]
                sp.start_playback(device_id=device_id, context_uri=album['uri'])
                return f"Playing album: {album['name']}."

            return "I couldn't find that on Spotify."
        except Exception as e:
            return f"Spotify search error: {e}"

    return "Sorry, I didn't understand the Spotify command."

def ensure_spotify_running():
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and 'spotify' in proc.info['name'].lower():
            return
    spotify_path = r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe"
    if os.path.exists(spotify_path):
        try:
            subprocess.Popen(spotify_path)
            print("Spotify launched.")
        except Exception as e:
            print(f"Failed to launch Spotify: {e}")
    else:
        print("[Spotify] Spotify.exe not found at expected path.")

# ─── Weather ──────────────────────────────────────────────────────────────────

def get_weather(city):
    api_key = OPENWEATHER_KEY
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if res.status_code != 200:
            return f"Could not get weather: {data.get('message', 'Unknown error')}"
        temp = data['main']['temp']
        weather = data['weather'][0]['description']
        return f"The weather in {city} is {weather} with a temperature of {temp} degrees Celsius."
    except Exception as e:
        return f"Error fetching weather: {e}"

# ─── Audio ────────────────────────────────────────────────────────────────────

def beep():
    try:
        winsound.Beep(1000, 500)
    except Exception:
        pass

def calibrate_ambient_noise():
    try:
        with sr.Microphone() as source:
            print("Calibrating ambient noise, please wait...")
            recognizer.adjust_for_ambient_noise(source, duration=1)
            print("Calibration complete.")
    except Exception as e:
        print(f"[Calibration] Error: {e}")

def listen():
    try:
        with sr.Microphone() as source:
            print("Listening...")
            audio = recognizer.listen(source, timeout=30, phrase_time_limit=8)
            try:
                command = recognizer.recognize_google(audio)
                print(f"You said: {command}")
                return command.lower()
            except sr.UnknownValueError:
                print("Could not understand audio")
                return ""
            except sr.RequestError as e:
                print(f"Speech API error: {e}")
                return ""
    except sr.WaitTimeoutError:
        print("Listening timed out.")
        return ""
    except Exception as e:
        print(f"[Listen] Error: {e}")
        return ""

def speak(text):
    bridge.notify_speaking(text)
    print(f"[JARVIS]: {text}")
    _tts_queue.put(text)
    _tts_queue.join()
    bridge.notify_done()

# ─── App Control ──────────────────────────────────────────────────────────────

def handle_app_command(command):
    app_map = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "spotify": r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe",
        "whatsapp": "explorer shell:appsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
        "games": r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe"
    }

    command_lower = command.lower()
    print(f"Processing app command: {command_lower}")

    if "open" in command_lower or "start" in command_lower or "launch" in command_lower:
        for name, path in app_map.items():
            if name in command_lower:
                try:
                    if path.startswith("explorer"):
                        os.system(path)
                    else:
                        if os.path.exists(path) or path in ('notepad.exe', 'calc.exe'):
                            subprocess.Popen(path)
                        else:
                            return f"Could not find {name} at {path}."
                    return f"Opening {name}."
                except Exception as e:
                    return f"Failed to open {name}: {e}"

    elif "close" in command_lower or "exit" in command_lower:
        for name, path in app_map.items():
            if name in command_lower:
                process_name = os.path.basename(path)
                if not process_name.endswith(".exe"):
                    return f"Cannot close {name} this way."
                os.system(f"taskkill /f /im {process_name}")
                return f"Closed {name}."

    return "Sorry, I couldn't identify the app to open or close."

# ─── Calendar (disabled) ──────────────────────────────────────────────────────

def create_calendar_event(summary, start_time_str, duration_minutes=30):
    return f"Calendar feature is currently disabled. Event '{summary}' noted for {start_time_str}."

# ─── Timer / Alarm ────────────────────────────────────────────────────────────

def timer_alert(duration_seconds):
    time.sleep(duration_seconds)
    speak(f"Timer finished after {duration_seconds // 60} minutes and {duration_seconds % 60} seconds.")
    beep()

def set_timer(command):
    pattern = r"(\d+)\s*(seconds|second|minutes|minute|hours|hour)"
    match = re.search(pattern, command)
    if not match:
        speak("For how long should I set the timer?")
        duration_text = listen()
        match = re.search(pattern, duration_text)

    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        seconds = 0
        if "second" in unit:
            seconds = amount
        elif "minute" in unit:
            seconds = amount * 60
        elif "hour" in unit:
            seconds = amount * 3600
        threading.Thread(target=timer_alert, args=(seconds,), daemon=True).start()
        speak(f"Timer set for {amount} {unit}.")
    else:
        speak("I couldn't understand the timer duration.")

def alarm_alert(alarm_time):
    now = datetime.datetime.now()
    delay = (alarm_time - now).total_seconds()
    if delay < 0:
        speak("That time is already past.")
        return
    time.sleep(delay)
    speak(f"Alarm ringing for {alarm_time.strftime('%H:%M')}")
    beep()

def set_alarm(command):
    speak("What time should I set the alarm for?")
    time_text = listen()
    alarm_time = dateparser.parse(time_text)
    if alarm_time:
        now = datetime.datetime.now()
        alarm_time = alarm_time.replace(year=now.year, month=now.month, day=now.day)
        if alarm_time < now:
            alarm_time += datetime.timedelta(days=1)
        threading.Thread(target=alarm_alert, args=(alarm_time,), daemon=True).start()
        speak(f"Alarm set for {alarm_time.strftime('%I:%M %p')}")
    else:
        speak("I couldn't understand the alarm time.")

# ─── Ollama LLM Fallback ──────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "phi3:mini"   # Change to whatever model you have pulled

def ollama_respond(prompt: str) -> str:
    """Send prompt to local Ollama. Returns a fallback string on failure."""
    try:
        response = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 200, "temperature": 0.7}
        }, timeout=10)
        if response.status_code == 200:
            reply = response.json().get("response", "").strip()
            if reply:
                print(f"[Ollama] Response received.")
                return reply
    except Exception as e:
        print(f"[Ollama] Error: {e}")
    return "I couldn't reach my thinking module. Is Ollama running?"

# ─── Fallback ─────────────────────────────────────────────────────────────────

_FALLBACK_RESPONSES = [
    "I'm not sure how to help with that. Could you rephrase?",
    "I didn't quite catch that. Try asking about weather, music, apps, or timers.",
    "I can help with weather, Spotify, opening apps, timers, and alarms. What would you like?",
    "Sorry, I don't have an answer for that right now.",
]
_fallback_idx = 0

def simple_fallback(user_message):
    global _fallback_idx
    print(f"[Fallback] No intent matched for: '{user_message}' — trying Ollama")
    reply = ollama_respond(user_message)
    # If Ollama is unavailable, cycle through canned responses
    if "couldn't reach" in reply:
        response = _FALLBACK_RESPONSES[_fallback_idx % len(_FALLBACK_RESPONSES)]
        _fallback_idx += 1
        return response
    return reply

# ─── Rule-Based Intent Detection ──────────────────────────────────────────────

def rule_based_intent_detection(command):
    command_lower = command.lower()

    greeting_words = ["hi", "hello", "hey", "good morning", "good evening", "good afternoon"]
    if any(word in command_lower for word in greeting_words):
        return "greeting", 0.95

    goodbye_words = ["bye", "goodbye", "see you", "quit", "exit", "shutdown", "turn off"]
    if any(word in command_lower for word in goodbye_words):
        return "goodbye", 0.95

    if any(word in command_lower for word in ["open", "start", "launch", "run"]):
        app_names = ["chrome", "browser", "whatsapp", "spotify", "notepad", "calculator",
                     "visual studio", "vs code", "games", "music app", "chat app"]
        if any(app in command_lower for app in app_names):
            return "app_control", 0.95

    if any(word in command_lower for word in ["close", "exit", "stop", "shut down", "kill"]):
        app_names = ["chrome", "browser", "whatsapp", "spotify", "notepad", "calculator",
                     "visual studio", "vs code", "games", "application", "app", "program"]
        if any(app in command_lower for app in app_names):
            return "close_app", 0.95

    play_indicators = ["play", "start", "resume", "continue", "turn on"]
    music_indicators = ["music", "song", "songs", "spotify", "track", "tracks", "playlist", "audio"]
    if any(play in command_lower for play in play_indicators):
        if any(music in command_lower for music in music_indicators) or command_lower.strip() == "play":
            return "play_spotify", 0.95

    pause_indicators = ["pause", "stop", "halt", "mute", "silence"]
    if any(pause in command_lower for pause in pause_indicators):
        if any(music in command_lower for music in music_indicators) or "spotify" in command_lower:
            return "pause_spotify", 0.95

    next_indicators = ["next", "skip", "forward", "change"]
    if any(next_word in command_lower for next_word in next_indicators):
        if any(music in command_lower for music in ["song", "track", "music"]) or \
           any(phrase in command_lower for phrase in ["next song", "skip song", "skip track"]):
            return "next_song", 0.95

    prev_indicators = ["previous", "last", "back", "rewind", "backward"]
    if any(prev in command_lower for prev in prev_indicators):
        if any(music in command_lower for music in ["song", "track", "music"]) or \
           any(phrase in command_lower for phrase in ["previous song", "last song", "go back"]):
            return "previous_song", 0.95

    weather_indicators = ["weather", "temperature", "raining", "sunny", "hot", "cold", "forecast"]
    if any(weather in command_lower for weather in weather_indicators):
        return "get_weather", 0.95

    timer_indicators = ["timer", "countdown", "remind me", "alert me", "wake me"]
    time_patterns = ["minutes", "minute", "hours", "hour", "seconds", "second"]
    if any(timer in command_lower for timer in timer_indicators) or \
       (any(t in command_lower for t in time_patterns) and "set" in command_lower):
        return "set_timer", 0.95

    calendar_indicators = ["schedule", "meeting", "appointment", "calendar", "event", "book"]
    if any(cal in command_lower for cal in calendar_indicators):
        if any(word in command_lower for word in ["meeting", "appointment", "event", "calendar"]):
            return "create_calendar_event", 0.95

    if "whatsapp" in command_lower or \
       (any(msg in command_lower for msg in ["message", "text", "send"]) and
            any(target in command_lower for target in ["mom", "dad", "friend", "someone"])):
        if not any(word in command_lower for word in ["open", "start", "launch"]):
            return "send_whatsapp", 0.95

    return None, 0.0

# ─── Main Assistant Loop ──────────────────────────────────────────────────────

def enhanced_assistant(wake_word="jarvis", access_key=PICOVOICE_ACCESS_KEY):
    calibrate_ambient_noise()
    print("Assistant is on. Say the wake word to start.")
    active_session = False

    sp = get_spotify_client()

    while True:
        if not active_session:
            print("Listening for wake word...")
            listen_for_wake_word(wake_word, access_key)
            beep()
            bridge.notify_listening()
            speak("How can I help you?")
            active_session = True
        else:
            print("Listening for command...")
            command = listen()

            if command == "":
                continue

            bridge.notify_processing()
            rule_intent, rule_confidence = rule_based_intent_detection(command)

            if rule_intent and rule_confidence > 0.9:
                intent, confidence = rule_intent, rule_confidence
                print(f"[Rule-based] '{command}' -> Intent: '{intent}' (confidence: {confidence:.2f})")
            else:
                intent, confidence = predict_intent(command)
                print(f"[ML Model] '{command}' -> Intent: '{intent}' (confidence: {confidence:.2f})")

            if "exit" in command.lower() or "stop listening" in command.lower():
                speak("Exiting session.")
                active_session = False
                continue

            MIN_CONFIDENCE = 0.20

            if confidence < MIN_CONFIDENCE:
                print(f"[Low confidence ({confidence:.2f})] Routing to Ollama")
                response = simple_fallback(command)
                speak(response)
                continue

            try:
                if intent == "goodbye":
                    speak("Shutting down. Goodbye!")
                    os._exit(0)

                elif intent == "greeting":
                    speak("Hello! How can I help you?")

                elif intent == "app_control":
                    print("[App Control] Handling open app command")
                    app_response = handle_app_command(command)
                    speak(app_response)

                elif intent == "close_app":
                    print("[App Control] Handling close app command")
                    app_response = handle_app_command(command)
                    speak(app_response)

                elif intent == "play_spotify":
                    print("[Spotify] Handling play command")
                    ensure_spotify_running()
                    time.sleep(3)
                    sp = get_spotify_client()
                    spotify_response = handle_spotify_commands(command, sp)
                    speak(spotify_response)

                elif intent == "pause_spotify":
                    print("[Spotify] Handling pause command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    spotify_response = handle_spotify_commands(command, sp)
                    speak(spotify_response)

                elif intent == "next_song":
                    print("[Spotify] Handling next song command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    device_id = get_active_device_id(sp)
                    if device_id:
                        sp.next_track(device_id=device_id)
                        speak("Skipping to next song.")
                    else:
                        speak("Spotify is not active.")

                elif intent == "previous_song":
                    print("[Spotify] Handling previous song command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    device_id = get_active_device_id(sp)
                    if device_id:
                        sp.previous_track(device_id=device_id)
                        speak("Playing previous song.")
                    else:
                        speak("Spotify is not active.")

                elif intent == "get_weather":
                    print("[Weather] Handling weather command")
                    city = None
                    if "in" in command:
                        city = command.split("in")[-1].strip()
                    if not city:
                        speak("Which city's weather would you like?")
                        city = listen().strip()
                    if city:
                        response = get_weather(city)
                        speak(response)
                    else:
                        speak("I didn't catch the city name.")

                elif intent == "create_calendar_event":
                    print("[Calendar] Handling calendar event command")
                    speak("What is the event?")
                    summary = listen()
                    if summary:
                        speak("When should I set it?")
                        date_input = listen()
                        parsed_time = dateparser.parse(date_input)
                        if parsed_time:
                            response = create_calendar_event(summary, parsed_time.strftime("%Y-%m-%d %H:%M:%S"))
                            speak(response)
                        else:
                            speak("I couldn't understand the date and time. Please try again.")
                    else:
                        speak("I didn't catch the event details.")

                elif intent == "set_timer":
                    print("[Timer] Handling timer command")
                    set_timer(command)

                elif intent == "send_whatsapp":
                    print("[WhatsApp] Handling WhatsApp command")
                    whatsapp_response = handle_app_command("open whatsapp")
                    speak(whatsapp_response)
                    speak("WhatsApp is now open. You can send your message.")

                else:
                    print(f"[Ollama] Unknown intent '{intent}' — routing to LLM")
                    response = simple_fallback(command)
                    speak(response)

            except Exception as e:
                print(f"[Error] Handling intent '{intent}': {e}")
                speak("Sorry, I encountered an error processing that request.")

if __name__ == "__main__":
    enhanced_assistant(
        wake_word="jarvis",
        access_key=PICOVOICE_ACCESS_KEY
    )
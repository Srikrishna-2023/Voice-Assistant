import os
import tempfile
import uuid
import subprocess
from gtts import gTTS
import speech_recognition as sr
from playsound import playsound
import winsound
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import time
import pygame
import dateparser
import threading
import datetime
import pickle
import nltk
from nltk.stem import WordNetLemmatizer
from nltk.tokenize import word_tokenize
import psutil  # for checking processes
nltk.data.path.append('C:/Users/srikr/nltk_data')
nltk.download('punkt')
nltk.download('wordnet')
import pvporcupine
import pyaudio
import struct

lemmatizer = WordNetLemmatizer()

recognizer = sr.Recognizer()

# Load your trained intent classifier and vectorizer
with open(r'C:\Users\srikr\Desktop\JARVIS\intent_model.pkl','rb') as f:
    model, vectorizer = pickle.load(f)


def listen_for_wake_word(keyword="jarvis", access_key="wqvkg+34J4wU8cl3lRuY76yrBW320W4xbV3hrJ6BmpdIEtHWq6gGfw=="):
    porcupine = pvporcupine.create(
        access_key=access_key,
        keywords=[keyword]
    )

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


def preprocess_text(text):
    tokens = word_tokenize(text)
    tokens = [lemmatizer.lemmatize(token.lower()) for token in tokens]
    return ' '.join(tokens)

def predict_intent(text):
    processed = preprocess_text(text)
    vector = vectorizer.transform([processed])
    prediction = model.predict(vector)
    probability = model.predict_proba(vector)
    confidence = max(probability[0])
    print(f"Intent: {prediction[0]}, Confidence: {confidence:.2f}")
    return prediction[0], confidence

# Spotify credentials
CLIENT_ID = '44cd2a8e7b444e3c91577b1d90a44687'
CLIENT_SECRET = '6bf50e8c3c6341928d96ac6eb3dc232d'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

def get_weather(city):
    api_key = "82e01f201388d48b9bafb0a900e47cbf"
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
    try:
        res = requests.get(url)
        data = res.json()
        if res.status_code != 200:
            return f"Could not get weather: {data.get('message', 'Unknown error')}"
        temp = data['main']['temp']
        weather = data['weather'][0]['description']
        return f"The weather in {city} is {weather} with a temperature of {temp}C."
    except Exception as e:
        return f"Error fetching weather: {e}"

def beep():
    duration = 500
    freq = 1000
    winsound.Beep(freq, duration)

def calibrate_ambient_noise():
    with sr.Microphone() as source:
        print("Calibrating ambient noise, please wait...")
        recognizer.adjust_for_ambient_noise(source, duration=1)
        print("Calibration complete.")

def listen():
    with sr.Microphone() as source:
        print("Listening...")
        audio = recognizer.listen(source, timeout=100, phrase_time_limit=5)
        try:
            command = recognizer.recognize_google(audio)
            print(f"You said: {command}")
            return command.lower()
        except sr.UnknownValueError:
            print("Could not understand audio")
            return ""
        except sr.RequestError as e:
            print(f"API error: {e}")
            return ""

def speak(text):
    tts = gTTS(text=text, lang='en')
    temp_dir = tempfile.gettempdir()
    filename = f"voice_{uuid.uuid4().hex}.mp3"
    filepath = os.path.join(temp_dir, filename)
    
    try:
        tts.save(filepath)
        pygame.mixer.init()
        pygame.mixer.music.load(filepath)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            continue
        pygame.mixer.quit()
        print("Finished playing audio.")
    except Exception as e:
        print(f"Error during speak: {e}")

conversation_history = []

def query_gpt4all(user_message):
    import requests
    
    GPT4ALL_API_URL = "http://localhost:4891/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    global conversation_history
    
    # Add the new user message to the conversation history
    conversation_history.append({"role": "user", "content": user_message})

    # Limit history length to last 10 messages (5 user + 5 assistant)
    max_history_length = 10
    messages_to_send = conversation_history[-max_history_length:]

    data = {
        "model": "default",
        "messages": messages_to_send,
        "max_tokens": 200
    }

    try:
        print("Sending request to GPT4All...")
        response = requests.post(GPT4ALL_API_URL, headers=headers, json=data, timeout=60)
        assistant_reply = response.json()['choices'][0]['message']['content']
        
        # Add assistant response to the conversation history
        conversation_history.append({"role": "assistant", "content": assistant_reply})

        return assistant_reply

    except requests.exceptions.ConnectionError:
        return "Could not connect to GPT4All. Is the local server enabled?"
    except requests.exceptions.Timeout:
        return "GPT4All server timed out. Try again."
    except Exception as e:
        return f"Unexpected error: {e}"

def get_spotify_client():
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=False,
        cache_path=".spotipy_cache"
    )
    token_info = sp_oauth.get_cached_token()
    if not token_info:
        auth_url = sp_oauth.get_authorize_url()
        print("Go to the following URL and authorize the app:")
        print(auth_url)
        redirected_url = input("Paste the full redirect URL here:\n")
        code = sp_oauth.parse_response_code(redirected_url)
        token_info = sp_oauth.get_access_token(code)
    access_token = token_info['access_token']
    return spotipy.Spotify(auth=access_token)

def get_active_device_id(sp):
    devices = sp.devices()
    for device in devices['devices']:
        if device['is_active']:
            return device['id']
    return devices['devices'][0]['id'] if devices['devices'] else None

def handle_spotify_commands(command, sp):
    device_id = get_active_device_id(sp)
    if not device_id:
        return "Spotify is not open or no active device found."

    command_lower = command.lower()

    if "pause" in command_lower:
        sp.pause_playback(device_id=device_id)
        return "Playback paused."

    if "resume" in command_lower or "play" == command_lower.strip():
        sp.start_playback(device_id=device_id)
        return "Resuming playback."

    # Extract query after 'play' for searching songs, playlists, albums
    if "play" in command_lower:
        query = command_lower.replace("play", "").strip()

        # Try to search for track
        track_results = sp.search(q=query, type='track', limit=1)
        if track_results['tracks']['items']:
            track = track_results['tracks']['items'][0]
            sp.start_playback(device_id=device_id, uris=[track['uri']])
            return f"Playing track: {track['name']} by {track['artists'][0]['name']}."

        # If no track found, try playlist
        playlist_results = sp.search(q=query, type='playlist', limit=1)
        if playlist_results['playlists']['items']:
            playlist = playlist_results['playlists']['items'][0]
            sp.start_playback(device_id=device_id, context_uri=playlist['uri'])
            return f"Playing playlist: {playlist['name']}."

        # If no playlist found, try album
        album_results = sp.search(q=query, type='album', limit=1)
        if album_results['albums']['items']:
            album = album_results['albums']['items'][0]
            sp.start_playback(device_id=device_id, context_uri=album['uri'])
            return f"Playing album: {album['name']}."

        return "I couldn't find that on Spotify."

    return "Sorry, I didn't understand the Spotify command."

def handle_app_command(command):
    app_map = {
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "spotify": r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe",
        "whatsapp": "explorer shell:appsFolder\\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
        "games":r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe"
        # Add more apps as needed
    }

    command_lower = command.lower()
    print(f"Processing app command: {command_lower}")

    if "open" in command_lower:
        for name in app_map:
            if name in command_lower:
                path = app_map[name]
                try:
                    if path.startswith("explorer"):
                        os.system(path)  # UWP apps
                    else:
                        subprocess.Popen(path)  # Regular apps
                    return f"Opening {name}."
                except Exception as e:
                    return f"Failed to open {name}: {e}"

    elif "close" in command_lower:
        for name in app_map:
            if name in command_lower:
                process_name = os.path.basename(app_map[name])  # Extract process name
                if not process_name.endswith(".exe"):
                    return f"Cannot close {name}. UWP apps can't be closed this way."
                os.system(f"taskkill /f /im {process_name}")
                return f"Closed {name}."

    return "Sorry, I couldn't identify the app to open or close."

def ensure_spotify_running():
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] and 'spotify' in proc.info['name'].lower():
            return  # Spotify is already running

    # Spotify not running, try to open it
    spotify_path = r"C:\Users\srikr\AppData\Roaming\Spotify\Spotify.exe"
    try:
        subprocess.Popen(spotify_path)
        print("Spotify launched.")
    except Exception as e:
        print(f"Failed to launch Spotify: {e}")

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
import pytz

SCOPES = ['https://www.googleapis.com/auth/calendar']

def create_calendar_event(summary, start_time_str, duration_minutes=30):
    creds = None
    if os.path.exists('token.pkl'):
        with open('token.pkl', 'rb') as token:
            creds = pickle.load(token)
    else:
        flow = InstalledAppFlow.from_client_secrets_file('C:\\Users\\srikr\\Desktop\\credentials.json', SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        with open('token.pkl', 'wb') as token:
            pickle.dump(creds, token)

    service = build('calendar', 'v3', credentials=creds)

    timezone = pytz.timezone('Asia/Kolkata')
    start_time = timezone.localize(datetime.datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S"))
    end_time = start_time + datetime.timedelta(minutes=duration_minutes)

    event = {
        'summary': summary,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Kolkata'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Kolkata'},
    }

    event = service.events().insert(calendarId='primary', body=event).execute()
    return f"Event '{summary}' created for {start_time_str}"

def timer_alert(duration_seconds):
    time.sleep(duration_seconds)
    speak(f"Timer finished after {duration_seconds//60} minutes.")
    beep()

def set_timer(command):
    import re

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
def rule_based_intent_detection(command):
    """Enhanced rule-based intent detection for all features"""
    command_lower = command.lower()
    
    # Greeting patterns
    greeting_words = ["hi", "hello", "hey", "good morning", "good evening", "good afternoon"]
    if any(word in command_lower for word in greeting_words):
        return "greeting", 0.95
    
    # Goodbye patterns
    goodbye_words = ["bye", "goodbye", "see you", "quit", "exit", "shutdown", "turn off"]
    if any(word in command_lower for word in goodbye_words):
        return "goodbye", 0.95
    
    # App control patterns (opening apps)
    if any(word in command_lower for word in ["open", "start", "launch", "run"]):
        app_names = ["chrome", "browser", "whatsapp", "spotify", "notepad", "calculator", 
                    "visual studio", "vs code", "games", "music app", "chat app"]
        if any(app in command_lower for app in app_names):
            return "app_control", 0.95
    
    # Close app patterns  
    if any(word in command_lower for word in ["close", "exit", "stop", "shut down", "kill"]):
        app_names = ["chrome", "browser", "whatsapp", "spotify", "notepad", "calculator", 
                    "visual studio", "vs code", "games", "application", "app", "program"]
        if any(app in command_lower for app in app_names):
            return "close_app", 0.95
    
    # Spotify play patterns
    play_indicators = ["play", "start", "resume", "continue", "turn on"]
    music_indicators = ["music", "song", "songs", "spotify", "track", "tracks", "playlist", "audio"]
    if any(play in command_lower for play in play_indicators):
        if any(music in command_lower for music in music_indicators) or command_lower.strip() == "play":
            return "play_spotify", 0.95
    
    # Spotify pause patterns
    pause_indicators = ["pause", "stop", "halt", "mute", "silence"]
    if any(pause in command_lower for pause in pause_indicators):
        if any(music in command_lower for music in music_indicators) or "spotify" in command_lower:
            return "pause_spotify", 0.95
    
    # Next song patterns
    next_indicators = ["next", "skip", "forward", "change"]
    if any(next_word in command_lower for next_word in next_indicators):
        if any(music in command_lower for music in ["song", "track", "music"]) or \
           any(phrase in command_lower for phrase in ["next song", "skip song", "skip track"]):
            return "next_song", 0.95
    
    # Previous song patterns
    prev_indicators = ["previous", "last", "back", "rewind", "backward"]
    if any(prev in command_lower for prev in prev_indicators):
        if any(music in command_lower for music in ["song", "track", "music"]) or \
           any(phrase in command_lower for phrase in ["previous song", "last song", "go back"]):
            return "previous_song", 0.95
    
    # Weather patterns
    weather_indicators = ["weather", "temperature", "raining", "sunny", "hot", "cold", "forecast"]
    if any(weather in command_lower for weather in weather_indicators):
        return "get_weather", 0.95
    
    # Timer patterns
    timer_indicators = ["timer", "countdown", "remind me", "alert me", "wake me"]
    time_patterns = ["minutes", "minute", "hours", "hour", "seconds", "second"]
    if any(timer in command_lower for timer in timer_indicators) or \
       (any(time in command_lower for time in time_patterns) and "set" in command_lower):
        return "set_timer", 0.95
    
    # Calendar event patterns
    calendar_indicators = ["schedule", "meeting", "appointment", "calendar", "event", "book"]
    if any(cal in command_lower for cal in calendar_indicators):
        if any(word in command_lower for word in ["meeting", "appointment", "event", "calendar"]):
            return "create_calendar_event", 0.95
    
    # WhatsApp message patterns
    whatsapp_indicators = ["whatsapp", "message", "text", "chat", "send"]
    if "whatsapp" in command_lower or \
       (any(msg in command_lower for msg in ["message", "text", "send"]) and 
        any(target in command_lower for target in ["mom", "dad", "friend", "someone"])):
        # Only classify as send_whatsapp if it's clearly about messaging, not opening the app
        if not any(word in command_lower for word in ["open", "start", "launch"]):
            return "send_whatsapp", 0.95
    
    return None, 0.0

# Updated assistant function with better intent handling
def enhanced_assistant(wake_word="jarvis", access_key="wqvkg+34J4wU8cl3lRuY76yrBW320W4xbV3hrJ6BmpdIEtHWq6gGfw=="):
    calibrate_ambient_noise()
    print("Assistant is on. Say the wake word to start.")
    active_session = False

    sp = get_spotify_client()

    while True:
        if not active_session:
            print("Listening for wake word...")
            listen_for_wake_word(wake_word, access_key)
            beep()
            speak("How can I help you?")
            active_session = True
        else:
            print("Listening for command...")
            command = listen()

            if command == "":
                continue

            # First try rule-based detection
            rule_intent, rule_confidence = rule_based_intent_detection(command)
            
            if rule_intent and rule_confidence > 0.9:
                intent, confidence = rule_intent, rule_confidence
                print(f" Rule-based: '{command}' -> Intent: '{intent}' (confidence: {confidence:.2f})")
            else:
                # Fall back to ML model
                intent, confidence = predict_intent(command)
                print(f" ML Model: '{command}' -> Intent: '{intent}' (confidence: {confidence:.2f})")

            # TODO: Handle the intent here (call functions based on intent)
            # Example:
            # if intent == "play_music":
            #     play_music(sp)

            # Handle session control
            if "exit" in command.lower() or "stop listening" in command.lower():
                speak("Exiting session.")
                active_session = False
                continue

            # Set minimum confidence threshold
            MIN_CONFIDENCE = 0.20 # Lower threshold since we have rule-based backup
            
            if confidence < MIN_CONFIDENCE:
                print(f" Low confidence ({confidence:.2f}), falling back to GPT4All")
                response = query_gpt4all(command)
                speak(response)
                continue

            # Handle intents with high confidence
            try:
                if intent == "goodbye":
                    speak("Shutting down. Goodbye!")
                    break

                elif intent == "greeting":
                    speak("Hello! How can I help you?")
                    continue

                elif intent == "app_control":
                    print(" Handling app control command")
                    app_response = handle_app_command(command)
                    speak(app_response)
                    continue

                elif intent =="close_app":
                    print(" Handling close app command")
                    app_response = handle_app_command(command)
                    speak(app_response)
                    continue

                elif intent == "play_spotify":
                    print(" Handling Spotify play command")
                    ensure_spotify_running()
                    time.sleep(3)
                    sp = get_spotify_client()
                    spotify_response = handle_spotify_commands(command, sp)
                    speak(spotify_response)
                    continue

                elif intent == "pause_spotify":
                    print(" Handling Spotify pause command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    spotify_response = handle_spotify_commands(command, sp)
                    speak(spotify_response)
                    continue

                elif intent == "next_song":
                    print(" Handling next song command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    device_id = get_active_device_id(sp)
                    if device_id:
                        sp.next_track(device_id=device_id)
                        speak("Skipping to next song.")
                    else:
                        speak("Spotify is not active.")
                    continue

                elif intent == "previous_song":
                    print(" Handling previous song command")
                    ensure_spotify_running()
                    time.sleep(2)
                    sp = get_spotify_client()
                    device_id = get_active_device_id(sp)
                    if device_id:
                        sp.previous_track(device_id=device_id)
                        speak("Playing previous song.")
                    else:
                        speak("Spotify is not active.")
                    continue

                elif intent == "get_weather":
                    print(" Handling weather command")
                    if "in" in command:
                        city = command.split("in")[-1].strip()
                    else:
                        speak("Which city's weather?")
                        city = listen().strip()
                    if city:
                        response = get_weather(city)
                        speak(response)
                    else:
                        speak("I didn't catch the city name.")
                    continue

                elif intent == "create_calendar_event":
                    print(" Handling calendar event command")
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
                    continue

                elif intent == "set_timer":
                    print(" Handling timer command")
                    set_timer(command)
                    continue

                elif intent == "send_whatsapp":
                    print(" Handling WhatsApp message command")
                    # Open WhatsApp first
                    whatsapp_response = handle_app_command("open whatsapp")
                    speak(whatsapp_response)
                    speak("WhatsApp is now open. You can send your message.")
                    continue

                else:
                    # Fallback to GPT4All for unrecognized intents
                    print(f" Unknown intent '{intent}', using GPT4All")
                    response = query_gpt4all(command)
                    speak(response)

            except Exception as e:
                print(f" Error handling intent '{intent}': {e}")
                speak("Sorry, I encountered an error. Let me try a different approach.")
                response = query_gpt4all(command)
                speak(response)



if __name__ == "__main__":
    enhanced_assistant(wake_word="jarvis", access_key="wqvkg+34J4wU8cl3lRuY76yrBW320W4xbV3hrJ6BmpdIEtHWq6gGfw==")

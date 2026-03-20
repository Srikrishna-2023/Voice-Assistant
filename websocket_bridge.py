# websocket_bridge.py
# Compatible with websockets v16+
import asyncio
import json
import threading
import time

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    _WS_V16 = True
except ImportError:
    try:
        import websockets
        _WS_V16 = False
    except ImportError:
        websockets = None
        _WS_V16 = False


class AssistantIntegration:
    def __init__(self, assistant):
        self.assistant = assistant
        self.clients = set()
        self._loop = None

    async def handler(self, websocket):
        """Handle a single WebSocket connection."""
        self.clients.add(websocket)
        print(f"[WebSocket] Client connected. Total: {len(self.clients)}")
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get("type") == "text_command":
                        text = data.get("text", "")
                        print(f"[WebSocket] Text command received: {text}")
                        threading.Thread(
                            target=self._handle_text_command,
                            args=(text,),
                            daemon=True
                        ).start()
                    elif data.get("type") == "start_listening":
                        print("[WebSocket] Received start_listening from UI")
                except json.JSONDecodeError:
                    print(f"[WebSocket] Invalid JSON: {message}")
        except Exception as e:
            print(f"[WebSocket] Client disconnected: {e}")
        finally:
            self.clients.discard(websocket)
            print(f"[WebSocket] Client removed. Total: {len(self.clients)}")

    def _handle_text_command(self, text):
        """Process a text command from the UI using the full assistant intent handler."""
        from assistant import (
            rule_based_intent_detection, predict_intent, speak,
            handle_app_command, handle_spotify_commands, get_spotify_client,
            get_weather, set_timer, create_calendar_event, simple_fallback,
            ensure_spotify_running, get_active_device_id
        )

        self.notify_processing()

        rule_intent, rule_confidence = rule_based_intent_detection(text)
        if rule_intent and rule_confidence > 0.9:
            intent, confidence = rule_intent, rule_confidence
        else:
            intent, confidence = predict_intent(text)

        print(f"[WebSocket] Text intent: {intent} ({confidence:.2f})")

        try:
            if intent == "greeting":
                speak("Hello! How can I help you?")

            elif intent == "goodbye":
                speak("Goodbye!")
                import os
                os._exit(0)  # Force shutdown the entire process

            elif intent == "get_weather":
                city = None
                if "in" in text:
                    city = text.split("in")[-1].strip()
                if city:
                    response = get_weather(city)
                    speak(response)
                else:
                    speak("Which city's weather would you like?")

            elif intent in ("app_control", "close_app"):
                response = handle_app_command(text)
                speak(response)

            elif intent == "play_spotify":
                ensure_spotify_running()
                time.sleep(3)
                sp = get_spotify_client()
                response = handle_spotify_commands(text, sp)
                speak(response)

            elif intent == "pause_spotify":
                ensure_spotify_running()
                time.sleep(2)
                sp = get_spotify_client()
                response = handle_spotify_commands(text, sp)
                speak(response)

            elif intent == "next_song":
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
                ensure_spotify_running()
                time.sleep(2)
                sp = get_spotify_client()
                device_id = get_active_device_id(sp)
                if device_id:
                    sp.previous_track(device_id=device_id)
                    speak("Playing previous song.")
                else:
                    speak("Spotify is not active.")

            elif intent == "set_timer":
                set_timer(text)

            elif intent == "create_calendar_event":
                speak("Calendar feature is currently disabled.")

            elif intent == "send_whatsapp":
                response = handle_app_command("open whatsapp")
                speak(response)

            else:
                response = simple_fallback(text)
                speak(response)

        except Exception as e:
            print(f"[WebSocket] Error handling text command: {e}")
            speak("Sorry, I encountered an error processing that request.")

    def start_server(self, host="localhost", port=8080):
        """Start the WebSocket server in its own event loop (run in a thread)."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _run():
            if _WS_V16:
                async with ws_serve(self.handler, host, port) as server:
                    print(f"[WebSocket] Server listening at ws://{host}:{port}")
                    await asyncio.get_event_loop().create_future()
            else:
                async with websockets.serve(self.handler, host, port):
                    print(f"[WebSocket] Server listening at ws://{host}:{port}")
                    await asyncio.get_event_loop().create_future()

        try:
            self._loop.run_until_complete(_run())
        except Exception as e:
            print(f"[WebSocket] Server error: {e}")

    def _send_sync(self, message_dict):
        """Thread-safe send to all connected clients."""
        if not self.clients or self._loop is None:
            return
        json_msg = json.dumps(message_dict)

        async def _broadcast():
            dead = set()
            for client in list(self.clients):
                try:
                    await client.send(json_msg)
                except Exception:
                    dead.add(client)
            for c in dead:
                self.clients.discard(c)

        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(), self._loop)
        else:
            try:
                self._loop.run_until_complete(_broadcast())
            except Exception as e:
                print(f"[WebSocket] Broadcast error: {e}")

    def notify_listening(self):
        self._send_sync({"type": "status", "state": "listening"})

    def notify_processing(self):
        self._send_sync({"type": "status", "state": "processing"})

    def notify_speaking(self, text):
        self._send_sync({"type": "speaking", "text": text})

    def notify_done(self, text=""):
        self._send_sync({"type": "done", "text": text})

    def notify_error(self, error):
        self._send_sync({"type": "error", "message": error})
